"""Single-source-of-truth workflow engine shared by the FastAPI server and CLI.

The engine drives the 6-stage agent pipeline (normalizer → extracter → verifier →
generator → latexifier → evaluation/output). Callers wire in their own OpenAI
client, state-touch callback, blocking semaphore, and rubric loader so the engine
itself stays independent of HTTP and CLI concerns.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from agents import RunConfig, Runner, custom_span, trace
from agents.exceptions import (
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelRefusalError,
)
from agents.logger import logger
from openai import OpenAI

from ..artifacts import (
    AGENT_DISPLAY_NAMES,
    AGENT_METADATA_RELATIVE_PATH,
    WORKFLOW_AGENT_SEQUENCE,
    load_agent_input_payload,
    load_agent_output_payload,
    to_jsonable,
    write_agent_artifacts,
    write_workflow_metadata,
)
from ..container import SharedWorkflowRuntime, allowed_domains_from_env
from ..evaluation import (
    EVALUATION_DATASET_FILENAME,
    EVALUATION_ITEMS_FILENAME,
    EVALUATION_OUTPUTS_DIRNAME,
    EVALUATION_REPORT_FILENAME,
    EVALUATION_RUBRIC_FILENAME,
    EVALUATION_SPREADSHEET_FILENAME,
    run_openai_output_evaluation,
)
from ..helpers import (
    apply_requested_difficulty,
    build_json_message,
    merge_extracted_metadata_into_config,
    normalize_question_types,
    upload_file_to_openai,
)
from ..pipeline_validation import (
    validate_generator_output,
    validate_verifier_output,
)
from ..registry import agent_entry as _agent_entry
from ..registry import default_from_agent as _registry_default_from_agent
from ..registry import guardrail_content_node as _guardrail_content_node
from ..registry import guardrail_input_node as _guardrail_input_node
from ..registry import stoppable_agents as _registry_stoppable_agents
from ..safety import (
    LATEX_SUFFIXES,
    PDF_SUFFIXES,
    ensure_child_path,
    safe_filename,
    stable_short_hash,
)
from ..schemas import (
    ExtracterOutput,
    GeneratorOutput,
    LaTeXOutput,
    NormalizerOutput,
    VerifierOutput,
    WorkflowResult,
)
from .agents import container_expires_minutes, create_agents
from .guardrails import (
    GuardrailDecision,
    check_content_guardrails,
    check_input_guardrails,
)
from .payloads import (
    build_evaluation_final_result,
    build_extracter_input,
    build_extracter_retry_input,
    build_generator_input,
    build_generator_repair_input,
    build_latex_input,
    build_normalizer_input,
    build_style_review_input,
    build_verifier_input,
    build_verifier_repair_input,
)
from .resume import (
    agent_reasoning_level,
    coerce_extracter_from_agent_input,
    coerce_generator_from_agent_input,
    coerce_latex_from_agent_input,
    coerce_normalizer_from_agent_input,
    coerce_verifier_from_agent_input,
    extract_agent_input_payload,
    load_resume_agent_output,
    runner_input_from_upload,
    should_run_agent,
)
from .stage_cache import StageCache, file_content_hash

# ── Agent metadata derived from agents.json (lazy, no module-load side effects) ──


@lru_cache(maxsize=1)
def _key_by_name() -> dict[str, str]:
    """Map agent display names back to their canonical keys (plus "User")."""
    mapping = {v: k for k, v in AGENT_DISPLAY_NAMES.items()}
    mapping["User"] = "user"
    return mapping


@lru_cache(maxsize=1)
def _agent_flow() -> dict[str, tuple[str, str]]:
    """Resolve each agent's (receivedFrom, sentTo) display-node pair from the registry."""
    key_by_name = _key_by_name()
    return {
        key: (
            key_by_name.get(
                _agent_entry(key).get("inputFrom", ""),
                _agent_entry(key).get("inputFrom", "").lower(),
            ),
            key_by_name.get(
                _agent_entry(key).get("outputTo", ""),
                _agent_entry(key).get("outputTo", "").lower(),
            ),
        )
        for key in WORKFLOW_AGENT_SEQUENCE
    }


@lru_cache(maxsize=1)
def _eval_reasoning() -> str:
    """Evaluation reasoning effort from the registry (default "low")."""
    return _agent_entry("evaluation").get("reasoning", "low")


@lru_cache(maxsize=1)
def _default_resume_agent() -> str:
    """Registry-configured default resume agent (start of the pipeline)."""
    return _registry_default_from_agent()


def _max_turns(agent_key: str) -> int:
    """Per-stage max_turns from an OPTIONAL registry ``maxTurns`` field.

    Defaults to 12 for the tool-heavy latexifier (code interpreter + compile
    loop) and 6 for the pure output_type stages, so a malformed run fails fast
    instead of burning the SDK default.
    """
    default = 12 if agent_key == "latexifier" else 6
    return _agent_entry(agent_key).get("maxTurns", default)


def _is_container_expired_error(exc: BaseException) -> bool:
    """Heuristic: does this error indicate the shared container expired / vanished?

    Container-expiry surfaces as varied OpenAI/HTTP errors (404s, "container not
    found", "expired"). We recreate on any error whose text implicates the
    container so the latexifier gets one clean retry instead of failing the run.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if "container" not in text:
        return False
    if getattr(exc, "status_code", None) == 404:
        return True
    return any(
        marker in text
        for marker in ("expired", "not found", "no longer active", "does not exist")
    )


ToThread = Callable[..., Awaitable[Any]]
TouchWorkflow = Callable[[dict[str, Any]], None]
LoadRubricModel = Callable[[], str]
LoadRubricPayload = Callable[[], dict[str, Any]]
EmitToken = Callable[[str, str], None]  # (agent_key, text_delta)


async def run_llm_stage(
    agent: Any,
    input: Any,
    *,
    run_config: RunConfig,
    max_turns: int,
    stage_label: str,
    emit_token: Optional[EmitToken] = None,
) -> Any:
    """Run one LLM stage and return ``final_output``.

    Behavior is identical to a bare ``await Runner.run(...)`` when ``emit_token``
    is None (the mock-compatible non-streaming path). When ``emit_token`` is set,
    the run is streamed and each output-text delta is forwarded to the callback;
    a callback failure never breaks the run. SDK failures are normalized into a
    ``ValueError`` carrying the stage label so callers surface a clean error.
    """
    try:
        if emit_token is None:
            result = await Runner.run(
                agent, input=input, run_config=run_config, max_turns=max_turns
            )
            return result.final_output

        streamed = Runner.run_streamed(
            agent, input=input, run_config=run_config, max_turns=max_turns
        )
        async for event in streamed.stream_events():
            if getattr(event, "type", None) != "raw_response_event":
                continue
            data = getattr(event, "data", None)
            if getattr(data, "type", None) != "response.output_text.delta":
                continue
            delta = getattr(data, "delta", None)
            if not isinstance(delta, str):
                continue
            try:
                emit_token(stage_label, delta)
            except Exception:
                pass
        return streamed.final_output
    except (MaxTurnsExceeded, ModelBehaviorError, ModelRefusalError) as exc:
        raise ValueError(
            f"{stage_label} stage failed: {type(exc).__name__}: {exc}"
        ) from exc


def file_metadata(name: str, path: Path, *, downloadable: bool = True) -> dict[str, Any]:
    """Build API-facing file metadata without absolute local paths."""
    media_type, _ = mimetypes.guess_type(str(path))
    return {
        "id": stable_short_hash(name),
        "name": name,
        "size": path.stat().st_size if path.exists() else 0,
        "mediaType": media_type or "application/octet-stream",
        "downloadable": downloadable,
    }


def _log_task_exception(task: "asyncio.Task", label: str) -> None:
    """Retrieve a background task's exception so it never goes unobserved.

    Used for the LaTeX bootstrap task, which is awaited at the latexifier stage.
    If the run errors before that stage, this callback still consumes the
    exception (avoiding an "exception was never retrieved" warning) and logs it.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s failed: %s", label, exc)


def materialize_latex_output(
    latex_output: LaTeXOutput,
    outputs_dir: Path,
    shared_runtime: SharedWorkflowRuntime,
) -> tuple[LaTeXOutput, str, Path, str, Path]:
    """Write LaTeX safely, compile if needed, and return canonical paths."""
    latex_filename = safe_filename(
        latex_output.filename or "isomorphic_questions.tex",
        fallback="isomorphic_questions.tex",
        allowed_suffixes=LATEX_SUFFIXES,
    )
    latex_path = ensure_child_path(outputs_dir, latex_filename)
    latex_path.write_text(latex_output.latex_content, encoding="utf-8")

    pdf_filename = safe_filename(
        latex_output.pdf_filename or f"{Path(latex_filename).stem}.pdf",
        fallback="isomorphic_questions.pdf",
        allowed_suffixes=PDF_SUFFIXES,
    )
    pdf_path = ensure_child_path(outputs_dir, pdf_filename)
    if not pdf_path.exists():
        compile_result = shared_runtime.compile_latex(
            latex_output.latex_content,
            Path(latex_filename).stem or "isomorphic_questions",
        )
        latex_filename = safe_filename(
            compile_result.tex_filename,
            fallback=latex_filename,
            allowed_suffixes=LATEX_SUFFIXES,
        )
        pdf_filename = safe_filename(
            compile_result.pdf_filename,
            fallback=pdf_filename,
            allowed_suffixes=PDF_SUFFIXES,
        )
        latex_path = ensure_child_path(outputs_dir, latex_filename)
        pdf_path = ensure_child_path(outputs_dir, pdf_filename)

    updated = latex_output.model_copy(
        update={
            "filename": latex_filename,
            "pdf_filename": pdf_filename,
        }
    )
    return updated, latex_filename, latex_path, pdf_filename, pdf_path


def mark_provided_agent_state(
    workflow: dict[str, Any],
    *,
    agent_key: str,
    agent_output: Any,
    input_filename: str,
    start_agent: str,
) -> None:
    """Record upstream context reconstructed from an uploaded agent input file."""
    agent_state = workflow["agents"].get(agent_key)
    if not agent_state:
        return
    now = datetime.now().isoformat()
    agent_state["status"] = "completed"
    data = agent_state.setdefault("data", {})
    data["startTime"] = now
    data["endTime"] = now
    data["executionTime"] = 0.0
    data["receivedFrom"] = "uploaded_input"
    data["sentTo"] = start_agent
    data["input"] = f"Derived from uploaded agent input file: {input_filename}"
    data["output"] = to_jsonable(agent_output)


def record_provided_agent_artifact(
    *,
    outputs_dir: Path,
    workflow_id: str,
    agent_key: str,
    agent_output: Any,
    input_filename: str,
    start_agent: str,
) -> dict[str, Any]:
    """Write an artifact for reconstructed upstream context."""
    agent_name = AGENT_DISPLAY_NAMES.get(agent_key, agent_key.title())
    return write_agent_artifacts(
        outputs_dir=outputs_dir,
        workflow_id=workflow_id,
        agent_key=agent_key,
        agent_name=agent_name,
        input_payload={
            "task": "derive_context_from_uploaded_agent_input",
            "input_file": input_filename,
            "start_agent": start_agent,
        },
        output_payload=agent_output,
        metadata={
            "agent_key": agent_key,
            "agent_name": agent_name,
            "execution_time_seconds": 0.0,
            "received_from_node": "uploaded_input",
            "sent_to_node": start_agent,
            "input_summary": f"Context derived from {input_filename}",
        },
        status="provided",
    )


def mark_reused_agent_state(
    workflow: dict[str, Any],
    *,
    agent_key: str,
    agent_output: Any,
    source_folder: str,
) -> None:
    """Record that a workflow agent reused cached output from a prior run."""
    agent_state = workflow["agents"].get(agent_key)
    if not agent_state:
        return
    now = datetime.now().isoformat()
    agent_state["status"] = "completed"
    data = agent_state.setdefault("data", {})
    data["startTime"] = now
    data["endTime"] = now
    data["executionTime"] = 0.0
    data["receivedFrom"] = "cached"
    data["sentTo"] = "resume"
    data["input"] = f"Reused from outputs/{source_folder}"
    data["output"] = to_jsonable(agent_output)


def copy_or_record_reused_agent_artifact(
    *,
    outputs_dir: Path,
    workflow_id: str,
    agent_key: str,
    agent_output: Any,
    resume_source_dir: Path,
    resume_source_label: str,
) -> dict[str, Any]:
    """Persist artifacts for a resumed agent run using cached inputs when available."""
    agent_name = AGENT_DISPLAY_NAMES.get(agent_key, agent_key.title())
    cached_input = load_agent_input_payload(resume_source_dir, agent_key)
    if cached_input is None:
        cached_input = {
            "reused_from_output_folder": resume_source_label,
            "input_unavailable": True,
        }
    return write_agent_artifacts(
        outputs_dir=outputs_dir,
        workflow_id=workflow_id,
        agent_key=agent_key,
        agent_name=agent_name,
        input_payload=cached_input,
        output_payload=agent_output,
        metadata={
            "agent_key": agent_key,
            "agent_name": agent_name,
            "execution_time_seconds": 0.0,
            "received_from_node": "cached",
            "sent_to_node": "resume",
            "reused_from_output_folder": resume_source_label,
        },
        status="reused",
    )


def finalize_agent_run(
    *,
    workflow: dict[str, Any],
    outputs_dir: Path,
    workflow_id: str,
    agent_metadata_records: list[dict[str, Any]],
    touch_workflow: TouchWorkflow,
    agent_key: str,
    agent_name: str,
    elapsed: float,
    input_payload: Any,
    output_payload: Any,
    metadata: dict[str, Any],
) -> None:
    """Record a freshly-run agent stage: mark completed, store output, persist artifacts.

    ``output_payload`` is the pydantic stage output; its ``model_dump()`` is mirrored
    into the live workflow state for the UI while the model itself is archived.
    """
    state = workflow["agents"][agent_key]
    state["status"] = "completed"
    data = state["data"]
    data["endTime"] = datetime.now().isoformat()
    data["executionTime"] = elapsed
    data["output"] = output_payload.model_dump()
    touch_workflow(workflow)
    agent_metadata_records.append(
        write_agent_artifacts(
            outputs_dir=outputs_dir,
            workflow_id=workflow_id,
            agent_key=agent_key,
            agent_name=agent_name,
            input_payload=input_payload,
            output_payload=output_payload,
            metadata=metadata,
        )
    )


def resolve_upstream_agent(
    *,
    workflow: dict[str, Any],
    outputs_dir: Path,
    workflow_id: str,
    agent_metadata_records: list[dict[str, Any]],
    touch_workflow: TouchWorkflow,
    agent_key: str,
    is_input_to_agent_run: bool,
    resume_source_dir: Optional[Path],
    resume_source_label: str,
    agent_input_filename: str,
    start_agent: str,
    from_input: Callable[[], Any],
    from_cache: Callable[[], Any],
    post_process: Optional[Callable[[Any], None]] = None,
) -> Any:
    """Resolve a skipped upstream stage from an uploaded payload or cached run.

    Both branches build the agent output, apply the same optional in-place
    ``post_process`` (e.g. difficulty/metadata normalization), record state, and
    append the artifact record — the only differences are the source of the
    output and whether it is marked "provided" vs "reused".
    """
    if is_input_to_agent_run:
        output = from_input()
        if post_process is not None:
            post_process(output)
        mark_provided_agent_state(
            workflow,
            agent_key=agent_key,
            agent_output=output,
            input_filename=agent_input_filename,
            start_agent=start_agent,
        )
        touch_workflow(workflow)
        agent_metadata_records.append(
            record_provided_agent_artifact(
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_key=agent_key,
                agent_output=output,
                input_filename=agent_input_filename,
                start_agent=start_agent,
            )
        )
        return output

    if resume_source_dir is None:
        raise ValueError(f"resume_source_dir is required for cached {agent_key} output")

    output = from_cache()
    if post_process is not None:
        post_process(output)
    mark_reused_agent_state(
        workflow,
        agent_key=agent_key,
        agent_output=output,
        source_folder=resume_source_label,
    )
    touch_workflow(workflow)
    agent_metadata_records.append(
        copy_or_record_reused_agent_artifact(
            outputs_dir=outputs_dir,
            workflow_id=workflow_id,
            agent_key=agent_key,
            agent_output=output,
            resume_source_dir=resume_source_dir,
            resume_source_label=resume_source_label,
        )
    )
    return output


def _build_partial_result(
    *,
    normalizer_output: NormalizerOutput,
    extracter_output: Optional[ExtracterOutput] = None,
    verifier_output: Optional[VerifierOutput] = None,
    generator_output: Optional[GeneratorOutput] = None,
    latex_output: Optional[LaTeXOutput] = None,
) -> WorkflowResult:
    """Build a WorkflowResult from whatever stages completed before an early stop."""
    return WorkflowResult(
        config=normalizer_output,
        source_questions=extracter_output.questions if extracter_output else [],
        verified_questions=verifier_output.questions if verifier_output else [],
        generated_questions=generator_output.isomorphic_questions if generator_output else [],
        latex_file=latex_output.filename if latex_output else "",
        pdf_file=latex_output.pdf_filename if latex_output else "",
    )


def _begin_guardrail_node(
    workflow: dict[str, Any],
    node_key: str,
    *,
    received_from: str,
    sent_to: str,
    touch_workflow: TouchWorkflow,
) -> None:
    """Mark a guardrail display node as running (no-op when the node is absent)."""
    agents_map = workflow.get("agents", {})
    if node_key not in agents_map:
        return
    node = agents_map[node_key]
    node["status"] = "thinking"
    data = node.setdefault("data", {})
    data["startTime"] = datetime.now().isoformat()
    data.setdefault("receivedFrom", received_from)
    data.setdefault("sentTo", sent_to)
    touch_workflow(workflow)


def _complete_guardrail_node(
    workflow: dict[str, Any],
    node_key: str,
    *,
    touch_workflow: TouchWorkflow,
) -> None:
    """Mark a guardrail display node as passed (no-op when the node is absent)."""
    agents_map = workflow.get("agents", {})
    if node_key not in agents_map:
        return
    node = agents_map[node_key]
    node["status"] = "completed"
    data = node.setdefault("data", {})
    data["endTime"] = datetime.now().isoformat()
    data["output"] = {"allowed": True}
    touch_workflow(workflow)


async def _finalize_blocked(
    *,
    workflow: dict[str, Any],
    outputs_dir: Path,
    workflow_id: str,
    output_folder_label: str,
    touch_workflow: TouchWorkflow,
    to_thread: ToThread,
    decision: GuardrailDecision,
    agent_metadata_records: list[dict[str, Any]],
    normalizer_output: Optional[NormalizerOutput] = None,
) -> WorkflowResult:
    """Finalize a run that a guardrail blocked.

    Marks the workflow ``blocked``, records the decision, cancels any agents that
    had not yet completed, writes a minimal ``final_result.json``, notifies the
    caller, and returns the (empty) WorkflowResult.
    """
    logger.warning(
        "Workflow %s blocked by %s guardrail (category=%s): %s",
        workflow_id,
        decision.stage,
        decision.category,
        decision.reason,
    )
    workflow["status"] = "blocked"
    workflow["blocked"] = {
        "category": decision.category,
        "reason": decision.reason,
        "stage": decision.stage,
    }
    blocked_node = (
        _guardrail_input_node()
        if decision.stage == "input"
        else _guardrail_content_node()
        if decision.stage == "content"
        else ""
    )
    agents_map = workflow.get("agents", {})
    for node_key, agent_state in agents_map.items():
        if node_key == blocked_node:
            continue
        if agent_state.get("status") != "completed":
            agent_state["status"] = "cancelled"
    if blocked_node and blocked_node in agents_map:
        blocked_state = agents_map[blocked_node]
        blocked_state["status"] = "blocked"
        blocked_data = blocked_state.setdefault("data", {})
        blocked_data["endTime"] = datetime.now().isoformat()
        blocked_data["output"] = {
            "allowed": False,
            "category": decision.category,
            "reason": decision.reason,
        }

    final_result = WorkflowResult(
        config=normalizer_output or NormalizerOutput(),
        source_questions=[],
        verified_questions=[],
        generated_questions=[],
        latex_file="",
        pdf_file="",
    )
    outputs_dir.mkdir(parents=True, exist_ok=True)
    result_path = outputs_dir / "final_result.json"
    await to_thread(
        result_path.write_text,
        final_result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    workflow["outputFolder"] = output_folder_label
    workflow["result"] = final_result.model_dump()
    touch_workflow(workflow)
    return final_result


async def run_workflow_async(
    *,
    client: OpenAI,
    workflow_id: str,
    workflow: dict[str, Any],
    prompt: str,
    file_paths: list[str],
    outputs_dir: Path,
    output_folder_label: str,
    touch_workflow: TouchWorkflow,
    theme: str = "",
    to_thread: ToThread,
    load_rubric_model: LoadRubricModel,
    load_rubric_payload: LoadRubricPayload,
    resume_from_agent: str | None = None,
    resume_source_dir: Optional[Path] = None,
    resume_source_label: str = "",
    agent_input_override: Any = None,
    agent_input_filename: str = "",
    input_to_agent_run: bool = False,
    run_evaluation: bool = True,
    stop_after_agent: Optional[str] = None,
    emit_token: Optional[EmitToken] = None,
) -> WorkflowResult:
    """Run the workflow and write artifacts into a specific output directory.

    Returns the final WorkflowResult. The `workflow` dict is mutated in place to
    track stage status for callers (FastAPI) that surface it to the UI; CLI
    callers can pass a stub dict with the expected nested structure.

    When ``emit_token`` is provided the LLM stages stream their output-text deltas
    to it as ``(agent_key, delta)``; when None (the default) the stages run
    non-streaming, identical to the prior behavior.
    """
    resume_from_agent = resume_from_agent or _default_resume_agent()
    with trace("Isomorphic Question Generation Workflow"):
        if resume_from_agent not in WORKFLOW_AGENT_SEQUENCE:
            raise ValueError(f"Unknown resume_from_agent: {resume_from_agent}")
        _stoppable_agents = _registry_stoppable_agents()
        if stop_after_agent is not None and stop_after_agent not in _stoppable_agents:
            raise ValueError(
                f"stop_after_agent must be one of {_stoppable_agents}, got: {stop_after_agent!r}"
            )

        logger.info(
            "Workflow %s engine starting (resume_from=%s, files=%d)",
            workflow_id,
            resume_from_agent,
            len(file_paths),
        )
        outputs_dir.mkdir(parents=True, exist_ok=True)
        workflow["outputFolder"] = output_folder_label
        workflow["status"] = "running"
        touch_workflow(workflow)

        run_config = RunConfig(
            trace_metadata={
                "__trace_source__": "isomorphic-question-generator",
                "timestamp": output_folder_label.replace("/", "_"),
            }
        )
        agent_metadata_records: list[dict[str, Any]] = []
        stage_cache = StageCache()
        has_uploaded_agent_input = agent_input_override is not None
        is_input_to_agent_run = input_to_agent_run or has_uploaded_agent_input
        uploaded_agent_input = (
            runner_input_from_upload(agent_input_override)
            if has_uploaded_agent_input
            else None
        )
        uploaded_agent_payload = (
            extract_agent_input_payload(agent_input_override)
            if has_uploaded_agent_input
            else {}
        )
        workflow_type = (
            "input_to_agent"
            if is_input_to_agent_run
            else "rerun"
            if resume_source_dir
            else "full"
        )
        should_run_evaluation = (
            run_evaluation and should_run_agent("evaluation", resume_from_agent)
        )
        evaluation_was_skipped = (
            not should_run_evaluation
            and should_run_agent("evaluation", resume_from_agent)
        )
        # A "fresh full run" starts at the normalizer with no uploaded agent input
        # and no resume-from-cache source. Guardrails only apply to these runs;
        # resumes/replays already went through them on the original run.
        is_fresh_full_run = (
            should_run_agent("normalizer", resume_from_agent)
            and not is_input_to_agent_run
            and resume_source_dir is None
        )
        prompt_text = (prompt or "").strip()

        file_ids: list[str] = []
        file_names: list[str] = []
        file_content_hashes: list[str] = []
        file_paths_to_upload = (
            file_paths if should_run_agent("extracter", resume_from_agent) else []
        )
        if file_paths_to_upload:
            logger.info(
                "Workflow %s uploading %d source file(s) to OpenAI",
                workflow_id,
                len(file_paths_to_upload),
            )
        for file_path in file_paths_to_upload:
            path = Path(file_path)
            if path.exists():
                file_id = await to_thread(upload_file_to_openai, client, path, logger)
                file_ids.append(file_id)
                file_names.append(path.name)
                if stage_cache.enabled:
                    file_content_hashes.append(await to_thread(file_content_hash, path))
        if not file_names:
            file_names = [Path(path).name for path in file_paths]
        if file_paths_to_upload:
            logger.info(
                "Workflow %s finished file upload (%d file_ids)",
                workflow_id,
                len(file_ids),
            )

        # Input guardrails run BEFORE the container is created so a blocked input
        # never spins up an OpenAI container. Only fresh full runs are checked.
        if is_fresh_full_run:
            _begin_guardrail_node(
                workflow,
                _guardrail_input_node(),
                received_from="Starter",
                sent_to="Normalizer",
                touch_workflow=touch_workflow,
            )
            input_decision = await check_input_guardrails(
                client=client,
                prompt=prompt_text,
                file_names=file_names,
                to_thread=to_thread,
            )
            if not input_decision.allowed:
                return await _finalize_blocked(
                    workflow=workflow,
                    outputs_dir=outputs_dir,
                    workflow_id=workflow_id,
                    output_folder_label=output_folder_label,
                    touch_workflow=touch_workflow,
                    to_thread=to_thread,
                    decision=input_decision,
                    agent_metadata_records=agent_metadata_records,
                )
            _complete_guardrail_node(
                workflow,
                _guardrail_input_node(),
                touch_workflow=touch_workflow,
            )

        if not hasattr(client, "containers"):
            raise RuntimeError(
                "OpenAI SDK does not support containers; upgrade openai to >=2.0.0"
            )
        workflow["container"]["status"] = "creating"
        touch_workflow(workflow)
        logger.info("Workflow %s creating OpenAI container", workflow_id)
        container = await to_thread(
            client.containers.create,
            name=f"isomorphic-workflow-{workflow_id}-{output_folder_label.replace('/', '-')}",
            expires_after={"anchor": "last_active_at", "minutes": container_expires_minutes()},
            file_ids=file_ids,
        )
        shared_container_id = container.id
        workflow["container"]["status"] = "active"
        workflow["container"]["id"] = shared_container_id
        workflow["container"]["allowedDomains"] = allowed_domains_from_env()
        touch_workflow(workflow)
        logger.info(
            "Workflow %s container created (id=%s)",
            workflow_id,
            shared_container_id,
        )
        shared_runtime = SharedWorkflowRuntime(
            client=client,
            container_id=shared_container_id,
            output_dir=outputs_dir,
            allowed_domains=allowed_domains_from_env(),
        )

        # Keep the shared container from expiring while long, container-idle
        # stages (normalizer→generator) run: every half-expiry (bounded to
        # [30, 300]s) touch it via containers.retrieve, which refreshes
        # last_active_at. Failures just log so the run continues.
        heartbeat_interval = max(30, min(container_expires_minutes() * 60 // 2, 300))

        async def _container_heartbeat(container_id: str) -> None:
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await to_thread(client.containers.retrieve, container_id)
                except Exception as exc:
                    logger.warning(
                        "Workflow %s container heartbeat failed: %s", workflow_id, exc
                    )

        heartbeat_task = asyncio.ensure_future(_container_heartbeat(shared_container_id))

        # The post-container body is wrapped so the heartbeat is always cancelled
        # on every exit path (success, early return, or exception) — otherwise a
        # dangling background task keeps asyncio.run / tests from terminating.
        try:
            # Installing the LaTeX toolchain (apt-get texlive…) is the slow part of
            # setup, but only the latexifier needs it. Kick it off in the background
            # so normalizer→generator start as soon as the container exists instead
            # of blocking on it; it's awaited at the top of the latexifier stage.
            # compile_latex() also calls bootstrap() idempotently, so it installs
            # exactly once.
            latex_bootstrap_task: Optional[asyncio.Task] = None
            if should_run_agent("latexifier", resume_from_agent):
                latex_bootstrap_task = asyncio.ensure_future(
                    to_thread(shared_runtime.bootstrap)
                )
                latex_bootstrap_task.add_done_callback(
                    lambda task: _log_task_exception(
                        task, f"Workflow {workflow_id} LaTeX runtime bootstrap"
                    )
                )

            agents = create_agents(
                file_ids=file_ids,
                container_id=shared_container_id,
                latex_compile_tool=shared_runtime.build_compile_latex_tool(),
            )

            return await _run_pipeline_body(
                client=client,
                workflow_id=workflow_id,
                workflow=workflow,
                prompt_text=prompt_text,
                theme=theme,
                file_paths=file_paths,
                file_ids=file_ids,
                file_names=file_names,
                file_content_hashes=file_content_hashes,
                outputs_dir=outputs_dir,
                output_folder_label=output_folder_label,
                touch_workflow=touch_workflow,
                to_thread=to_thread,
                run_config=run_config,
                agents=agents,
                shared_runtime=shared_runtime,
                shared_container_id=shared_container_id,
                latex_bootstrap_task=latex_bootstrap_task,
                agent_metadata_records=agent_metadata_records,
                stage_cache=stage_cache,
                resume_from_agent=resume_from_agent,
                resume_source_dir=resume_source_dir,
                resume_source_label=resume_source_label,
                agent_input_filename=agent_input_filename,
                is_input_to_agent_run=is_input_to_agent_run,
                has_uploaded_agent_input=has_uploaded_agent_input,
                uploaded_agent_input=uploaded_agent_input,
                uploaded_agent_payload=uploaded_agent_payload,
                workflow_type=workflow_type,
                should_run_evaluation=should_run_evaluation,
                evaluation_was_skipped=evaluation_was_skipped,
                is_fresh_full_run=is_fresh_full_run,
                stop_after_agent=stop_after_agent,
                load_rubric_model=load_rubric_model,
                load_rubric_payload=load_rubric_payload,
                emit_token=emit_token,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass


async def _run_pipeline_body(
    *,
    client: OpenAI,
    workflow_id: str,
    workflow: dict[str, Any],
    prompt_text: str,
    theme: str,
    file_paths: list[str],
    file_ids: list[str],
    file_names: list[str],
    file_content_hashes: list[str],
    outputs_dir: Path,
    output_folder_label: str,
    touch_workflow: TouchWorkflow,
    to_thread: ToThread,
    run_config: RunConfig,
    agents: dict[str, Any],
    shared_runtime: SharedWorkflowRuntime,
    shared_container_id: str,
    latex_bootstrap_task: Optional[asyncio.Task],
    agent_metadata_records: list[dict[str, Any]],
    stage_cache: StageCache,
    resume_from_agent: str,
    resume_source_dir: Optional[Path],
    resume_source_label: str,
    agent_input_filename: str,
    is_input_to_agent_run: bool,
    has_uploaded_agent_input: bool,
    uploaded_agent_input: Any,
    uploaded_agent_payload: dict[str, Any],
    workflow_type: str,
    should_run_evaluation: bool,
    evaluation_was_skipped: bool,
    is_fresh_full_run: bool,
    stop_after_agent: Optional[str],
    load_rubric_model: LoadRubricModel,
    load_rubric_payload: LoadRubricPayload,
    emit_token: Optional[EmitToken],
) -> WorkflowResult:
    """Run stages normalizer→output once the container/agents/runtime exist.

    Split out of ``run_workflow_async`` so the container heartbeat can wrap this
    whole body in a try/finally without re-indenting the entire pipeline.
    """
    if True:
        # ---- Normalizer ----
        if should_run_agent("normalizer", resume_from_agent):
            logger.info("Workflow %s starting Normalizer", workflow_id)
            workflow["agents"]["normalizer"]["status"] = "thinking"
            workflow["agents"]["normalizer"]["data"]["startTime"] = datetime.now().isoformat()
            touch_workflow(workflow)

            normalizer_input_payload = build_normalizer_input(prompt_text, file_names, theme)
            normalizer_input = build_json_message(normalizer_input_payload)
            if has_uploaded_agent_input and resume_from_agent == "normalizer":
                normalizer_input = uploaded_agent_input
            workflow["agents"]["normalizer"]["data"]["receivedFrom"] = _agent_flow()["normalizer"][0]
            workflow["agents"]["normalizer"]["data"]["sentTo"] = _agent_flow()["normalizer"][1]
            workflow["agents"]["normalizer"]["data"]["input"] = (
                uploaded_agent_payload
                if has_uploaded_agent_input and resume_from_agent == "normalizer" and uploaded_agent_payload
                else {"prompt": prompt_text} if prompt_text else None
            )
            touch_workflow(workflow)

            start_time = time.time()
            normalizer_output: NormalizerOutput = await run_llm_stage(
                agents["normalizer"],
                normalizer_input,
                run_config=run_config,
                max_turns=_max_turns("normalizer"),
                stage_label="normalizer",
                emit_token=emit_token,
            )
            elapsed = time.time() - start_time

            finalize_agent_run(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="normalizer",
                agent_name=AGENT_DISPLAY_NAMES["normalizer"],
                elapsed=elapsed,
                input_payload=normalizer_input,
                output_payload=normalizer_output,
                metadata={
                    "model": agents["normalizer"].model,
                    "reasoning_level": agent_reasoning_level(agents["normalizer"]),
                    "execution_time_seconds": elapsed,
                    "received_from_node": _agent_flow()["normalizer"][0],
                    "sent_to_node": _agent_flow()["normalizer"][1],
                    "input_summary": f"{len(normalizer_input)} message(s)",
                },
            )
        else:
            normalizer_output = resolve_upstream_agent(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="normalizer",
                is_input_to_agent_run=is_input_to_agent_run,
                resume_source_dir=resume_source_dir,
                resume_source_label=resume_source_label,
                agent_input_filename=agent_input_filename,
                start_agent=resume_from_agent,
                from_input=lambda: coerce_normalizer_from_agent_input(uploaded_agent_payload),
                from_cache=lambda: load_resume_agent_output(resume_source_dir, "normalizer"),
            )

        if stop_after_agent == "normalizer":
            workflow["status"] = "completed"
            touch_workflow(workflow)
            return _build_partial_result(normalizer_output=normalizer_output)

        # ---- Extracter ----
        if should_run_agent("extracter", resume_from_agent):
            workflow["agents"]["extracter"]["status"] = "thinking"
            workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
            touch_workflow(workflow)

            file_refs = [{"type": "input_file", "file_id": fid} for fid in file_ids]
            extracter_input_payload = build_extracter_input(file_names, normalizer_output)
            extracter_input = (
                uploaded_agent_input
                if has_uploaded_agent_input and resume_from_agent == "extracter"
                else build_json_message(extracter_input_payload, file_refs=file_refs)
            )
            workflow["agents"]["extracter"]["data"]["receivedFrom"] = _agent_flow()["extracter"][0]
            workflow["agents"]["extracter"]["data"]["sentTo"] = _agent_flow()["extracter"][1]
            workflow["agents"]["extracter"]["data"]["input"] = (
                uploaded_agent_payload
                if has_uploaded_agent_input and resume_from_agent == "extracter" and uploaded_agent_payload
                else extracter_input_payload
            )
            touch_workflow(workflow)

            start_time = time.time()
            extracter_cache_key = (
                stage_cache.make_key(
                    "extracter",
                    extracter_input_payload,
                    model=agents["extracter"].model,
                    reasoning=agent_reasoning_level(agents["extracter"]),
                    instructions=getattr(agents["extracter"], "instructions", ""),
                    extra="|".join(file_content_hashes),
                )
                if stage_cache.enabled
                else ""
            )
            cached_extracter = stage_cache.lookup(extracter_cache_key)
            extracter_cache_hit = cached_extracter is not None
            if extracter_cache_hit:
                extracter_output: ExtracterOutput = ExtracterOutput.model_validate(cached_extracter)
                logger.info("Workflow %s reused cached Extracter output", workflow_id)
            else:
                extracter_output = await run_llm_stage(
                    agents["extracter"],
                    extracter_input,
                    run_config=run_config,
                    max_turns=_max_turns("extracter"),
                    stage_label="extracter",
                    emit_token=emit_token,
                )
                if file_ids and not extracter_output.questions:
                    retry_extract_payload = build_extracter_retry_input(file_names, normalizer_output)
                    retry_extract_input = build_json_message(
                        retry_extract_payload,
                        file_refs=file_refs,
                    )
                    extracter_input = retry_extract_input
                    extracter_input_payload = retry_extract_payload
                    extracter_output = await run_llm_stage(
                        agents["extracter"],
                        retry_extract_input,
                        run_config=run_config,
                        max_turns=_max_turns("extracter"),
                        stage_label="extracter",
                        emit_token=emit_token,
                    )
            elapsed = time.time() - start_time
            normalize_question_types(extracter_output.questions)
            if file_ids and not extracter_output.questions:
                raise ValueError("Extractor returned zero questions after retry.")
            if not extracter_cache_hit:
                stage_cache.store(
                    extracter_cache_key,
                    agent_key="extracter",
                    output=extracter_output.model_dump(),
                    model=agents["extracter"].model,
                    reasoning=agent_reasoning_level(agents["extracter"]),
                )
            merge_extracted_metadata_into_config(normalizer_output, extracter_output)
            workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
            logger.info(
                "Extractor metadata for workflow %s: title=%s theme=%s",
                workflow_id,
                extracter_output.title or "",
                extracter_output.theme or "",
            )

            finalize_agent_run(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="extracter",
                agent_name=AGENT_DISPLAY_NAMES["extracter"],
                elapsed=elapsed,
                input_payload=extracter_input,
                output_payload=extracter_output,
                metadata={
                    "model": agents["extracter"].model,
                    "reasoning_level": agent_reasoning_level(agents["extracter"]),
                    "execution_time_seconds": elapsed,
                    "received_from_node": _agent_flow()["extracter"][0],
                    "sent_to_node": _agent_flow()["extracter"][1],
                    "input_summary": f"{len(extracter_input)} message(s)",
                    "questions_count": len(extracter_output.questions),
                    "cache_hit": extracter_cache_hit,
                },
            )
        else:
            def _apply_extracter_metadata(output: ExtracterOutput) -> None:
                normalize_question_types(output.questions)
                merge_extracted_metadata_into_config(normalizer_output, output)
                workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()

            extracter_output = resolve_upstream_agent(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="extracter",
                is_input_to_agent_run=is_input_to_agent_run,
                resume_source_dir=resume_source_dir,
                resume_source_label=resume_source_label,
                agent_input_filename=agent_input_filename,
                start_agent=resume_from_agent,
                from_input=lambda: coerce_extracter_from_agent_input(uploaded_agent_payload),
                from_cache=lambda: load_resume_agent_output(resume_source_dir, "extracter"),
                post_process=_apply_extracter_metadata,
            )

        # Content guardrails run once the extracter/normalizer content exists, so
        # off-topic or non-question source material is caught before the verifier.
        # Fresh full runs only (resumes/replays already passed on the original run).
        if is_fresh_full_run:
            _begin_guardrail_node(
                workflow,
                _guardrail_content_node(),
                received_from="Extracter",
                sent_to="Verifier",
                touch_workflow=touch_workflow,
            )
            content_decision = await check_content_guardrails(
                client=client,
                extracter_output=extracter_output,
                normalizer_output=normalizer_output,
                to_thread=to_thread,
            )
            if not content_decision.allowed:
                return await _finalize_blocked(
                    workflow=workflow,
                    outputs_dir=outputs_dir,
                    workflow_id=workflow_id,
                    output_folder_label=output_folder_label,
                    touch_workflow=touch_workflow,
                    to_thread=to_thread,
                    decision=content_decision,
                    agent_metadata_records=agent_metadata_records,
                    normalizer_output=normalizer_output,
                )
            _complete_guardrail_node(
                workflow,
                _guardrail_content_node(),
                touch_workflow=touch_workflow,
            )

        if stop_after_agent == "extracter":
            workflow["status"] = "completed"
            touch_workflow(workflow)
            return _build_partial_result(
                normalizer_output=normalizer_output,
                extracter_output=extracter_output,
            )

        # ---- Verifier ----
        if should_run_agent("verifier", resume_from_agent):
            workflow["agents"]["verifier"]["status"] = "thinking"
            workflow["agents"]["verifier"]["data"]["startTime"] = datetime.now().isoformat()
            touch_workflow(workflow)

            verifier_input_payload = build_verifier_input(extracter_output)
            verifier_input = build_json_message(verifier_input_payload)
            if has_uploaded_agent_input and resume_from_agent == "verifier":
                verifier_input = uploaded_agent_input
            workflow["agents"]["verifier"]["data"]["receivedFrom"] = _agent_flow()["verifier"][0]
            workflow["agents"]["verifier"]["data"]["sentTo"] = _agent_flow()["verifier"][1]
            workflow["agents"]["verifier"]["data"]["input"] = (
                uploaded_agent_payload
                if has_uploaded_agent_input and resume_from_agent == "verifier" and uploaded_agent_payload
                else verifier_input_payload
            )
            touch_workflow(workflow)

            start_time = time.time()
            verifier_output: VerifierOutput = await run_llm_stage(
                agents["verifier"],
                verifier_input,
                run_config=run_config,
                max_turns=_max_turns("verifier"),
                stage_label="verifier",
                emit_token=emit_token,
            )
            elapsed = time.time() - start_time
            normalize_question_types(verifier_output.questions)
            apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
            verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
            if not verifier_report.valid:
                repair_questions = [
                    q for q in extracter_output.questions
                    if q.source_id in set(verifier_report.repair_source_ids)
                ]
                if repair_questions:
                    repair_payload = build_verifier_repair_input(
                        extracter_output,
                        repair_questions,
                        verifier_report.summary(),
                    )
                    with custom_span("verifier-repair"):
                        repair_output: VerifierOutput = await run_llm_stage(
                            agents["verifier"],
                            build_json_message(repair_payload),
                            run_config=run_config,
                            max_turns=_max_turns("verifier"),
                            stage_label="verifier",
                            emit_token=emit_token,
                        )
                    merged_questions = [
                        q for q in verifier_output.questions
                        if q.source_id not in set(verifier_report.repair_source_ids)
                    ] + repair_output.questions
                    verifier_output = VerifierOutput(questions=merged_questions)
                    normalize_question_types(verifier_output.questions)
                    apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
                    verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
                if not verifier_report.valid:
                    raise ValueError(f"Verifier output failed validation: {verifier_report.summary()}")

            finalize_agent_run(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="verifier",
                agent_name=AGENT_DISPLAY_NAMES["verifier"],
                elapsed=elapsed,
                input_payload=verifier_input,
                output_payload=verifier_output,
                metadata={
                    "model": agents["verifier"].model,
                    "reasoning_level": agent_reasoning_level(agents["verifier"]),
                    "execution_time_seconds": elapsed,
                    "received_from_node": _agent_flow()["verifier"][0],
                    "sent_to_node": _agent_flow()["verifier"][1],
                    "input_summary": f"{len(verifier_input)} message(s)",
                    "questions_count": len(verifier_output.questions),
                },
            )
        else:
            def _apply_verifier_post(output: VerifierOutput) -> None:
                normalize_question_types(output.questions)
                apply_requested_difficulty(output.questions, normalizer_output.difficulty)

            verifier_output = resolve_upstream_agent(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="verifier",
                is_input_to_agent_run=is_input_to_agent_run,
                resume_source_dir=resume_source_dir,
                resume_source_label=resume_source_label,
                agent_input_filename=agent_input_filename,
                start_agent=resume_from_agent,
                from_input=lambda: coerce_verifier_from_agent_input(uploaded_agent_payload, extracter_output),
                from_cache=lambda: load_resume_agent_output(resume_source_dir, "verifier"),
                post_process=_apply_verifier_post,
            )

        if stop_after_agent == "verifier":
            workflow["status"] = "completed"
            touch_workflow(workflow)
            return _build_partial_result(
                normalizer_output=normalizer_output,
                extracter_output=extracter_output,
                verifier_output=verifier_output,
            )

        verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
        if not verifier_report.valid:
            raise ValueError(f"Verifier output failed validation: {verifier_report.summary()}")

        # ---- Generator ----
        if should_run_agent("generator", resume_from_agent):
            workflow["agents"]["generator"]["status"] = "thinking"
            workflow["agents"]["generator"]["data"]["startTime"] = datetime.now().isoformat()
            touch_workflow(workflow)

            generator_input_payload = build_generator_input(normalizer_output, verifier_output)
            generator_input = build_json_message(generator_input_payload)
            if has_uploaded_agent_input and resume_from_agent == "generator":
                generator_input_payload = dict(uploaded_agent_payload)
                uploaded_requirements = generator_input_payload.get("requirements")
                if not isinstance(uploaded_requirements, dict):
                    uploaded_requirements = {}
                    generator_input_payload["requirements"] = uploaded_requirements
                uploaded_requirements.setdefault("preserve_source_style_and_verbosity", True)
                uploaded_requirements.setdefault("keep_verbose_open_ended_sources_verbose", True)
                uploaded_requirements.setdefault("do_not_compress_narrative_stems_into_short_directives", True)
                uploaded_requirements.setdefault("do_not_add_padding_to_concise_sources", True)
                uploaded_requirements.setdefault("self_review_style_before_return", True)
                generator_input = build_json_message(generator_input_payload)
            workflow["agents"]["generator"]["data"]["receivedFrom"] = _agent_flow()["generator"][0]
            workflow["agents"]["generator"]["data"]["sentTo"] = _agent_flow()["generator"][1]
            workflow["agents"]["generator"]["data"]["input"] = generator_input_payload
            touch_workflow(workflow)

            start_time = time.time()
            generator_cache_key = (
                stage_cache.make_key(
                    "generator",
                    generator_input_payload,
                    model=agents["generator"].model,
                    reasoning=agent_reasoning_level(agents["generator"]),
                    instructions=getattr(agents["generator"], "instructions", ""),
                )
                if stage_cache.enabled
                else ""
            )
            cached_generator = stage_cache.lookup(generator_cache_key)
            generator_cache_hit = cached_generator is not None
            if generator_cache_hit:
                generator_output: GeneratorOutput = GeneratorOutput.model_validate(cached_generator)
                logger.info("Workflow %s reused cached Generator output", workflow_id)
            else:
                generator_output = await run_llm_stage(
                    agents["generator"],
                    generator_input,
                    run_config=run_config,
                    max_turns=_max_turns("generator"),
                    stage_label="generator",
                    emit_token=emit_token,
                )

                # Guard against a flaky empty generation: retry once before the style
                # pass so a single degenerate response does not sink the whole run.
                if not generator_output.isomorphic_questions:
                    retry_generator_output = await run_llm_stage(
                        agents["generator"],
                        generator_input,
                        run_config=run_config,
                        max_turns=_max_turns("generator"),
                        stage_label="generator",
                        emit_token=emit_token,
                    )
                    if retry_generator_output.isomorphic_questions:
                        generator_output = retry_generator_output

                pre_review_output = generator_output
                style_review_payload = build_style_review_input(
                    normalizer_output, verifier_output, generator_output
                )
                style_review_input = build_json_message(style_review_payload)
                with custom_span("generator-style-review"):
                    reviewed_output = await run_llm_stage(
                        agents["generator"],
                        style_review_input,
                        run_config=run_config,
                        max_turns=_max_turns("generator"),
                        stage_label="generator",
                        emit_token=emit_token,
                    )
                # The style pass only revises wording; it must never drop questions.
                # If it returns fewer items than it was given (e.g. an empty response),
                # keep the pre-review generation instead of letting it wipe the set.
                if len(reviewed_output.isomorphic_questions) >= len(
                    pre_review_output.isomorphic_questions
                ):
                    generator_output = reviewed_output
                else:
                    generator_output = pre_review_output
                generator_report = validate_generator_output(
                    verifier_output.questions,
                    generator_output,
                    normalizer_output.new_questions_per_question,
                )
                if not generator_report.valid:
                    repair_questions = [
                        q for q in verifier_output.questions
                        if q.source_id in set(generator_report.repair_source_ids)
                    ]
                    repair_payload = build_generator_repair_input(
                        normalizer_output,
                        verifier_output,
                        generator_output,
                        repair_questions,
                        generator_report.summary(),
                    )
                    with custom_span("generator-repair"):
                        generator_output = await run_llm_stage(
                            agents["generator"],
                            build_json_message(repair_payload),
                            run_config=run_config,
                            max_turns=_max_turns("generator"),
                            stage_label="generator",
                            emit_token=emit_token,
                        )
                    generator_report = validate_generator_output(
                        verifier_output.questions,
                        generator_output,
                        normalizer_output.new_questions_per_question,
                    )
                if not generator_report.valid:
                    raise ValueError(f"Generator output failed validation: {generator_report.summary()}")

                apply_requested_difficulty(
                    generator_output.isomorphic_questions,
                    normalizer_output.difficulty,
                )
            elapsed = time.time() - start_time
            if not generator_cache_hit:
                stage_cache.store(
                    generator_cache_key,
                    agent_key="generator",
                    output=generator_output.model_dump(),
                    model=agents["generator"].model,
                    reasoning=agent_reasoning_level(agents["generator"]),
                )

            finalize_agent_run(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="generator",
                agent_name=AGENT_DISPLAY_NAMES["generator"],
                elapsed=elapsed,
                input_payload=generator_input,
                output_payload=generator_output,
                metadata={
                    "model": agents["generator"].model,
                    "reasoning_level": agent_reasoning_level(agents["generator"]),
                    "execution_time_seconds": elapsed,
                    "received_from_node": _agent_flow()["generator"][0],
                    "sent_to_node": _agent_flow()["generator"][1],
                    "input_summary": f"{len(generator_input)} message(s)",
                    "questions_count": len(generator_output.isomorphic_questions),
                    "cache_hit": generator_cache_hit,
                },
            )
        else:
            def _apply_generator_difficulty(output: GeneratorOutput) -> None:
                apply_requested_difficulty(output.isomorphic_questions, normalizer_output.difficulty)

            generator_output = resolve_upstream_agent(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="generator",
                is_input_to_agent_run=is_input_to_agent_run,
                resume_source_dir=resume_source_dir,
                resume_source_label=resume_source_label,
                agent_input_filename=agent_input_filename,
                start_agent=resume_from_agent,
                from_input=lambda: coerce_generator_from_agent_input(uploaded_agent_payload),
                from_cache=lambda: load_resume_agent_output(resume_source_dir, "generator"),
                post_process=_apply_generator_difficulty,
            )

        if stop_after_agent == "generator":
            workflow["status"] = "completed"
            touch_workflow(workflow)
            return _build_partial_result(
                normalizer_output=normalizer_output,
                extracter_output=extracter_output,
                verifier_output=verifier_output,
                generator_output=generator_output,
            )

        generator_report = validate_generator_output(
            verifier_output.questions,
            generator_output,
            normalizer_output.new_questions_per_question,
        )
        if not generator_report.valid:
            raise ValueError(f"Generator output failed validation: {generator_report.summary()}")

        # ---- LaTeX ----
        if should_run_agent("latexifier", resume_from_agent):
            workflow["agents"]["latexifier"]["status"] = "thinking"
            workflow["agents"]["latexifier"]["data"]["startTime"] = datetime.now().isoformat()
            touch_workflow(workflow)

            # Ensure the LaTeX toolchain finished installing before the latexifier
            # compiles. By now normalizer→generator have run, so this usually
            # returns immediately; any bootstrap failure surfaces here, attributed
            # to the latexifier stage.
            if latex_bootstrap_task is not None:
                await latex_bootstrap_task

            latex_input_payload = build_latex_input(
                normalizer_output, extracter_output, generator_output
            )
            latex_input = build_json_message(latex_input_payload)
            if has_uploaded_agent_input and resume_from_agent == "latexifier":
                latex_input = uploaded_agent_input
            workflow["agents"]["latexifier"]["data"]["receivedFrom"] = _agent_flow()["latexifier"][0]
            workflow["agents"]["latexifier"]["data"]["sentTo"] = (
                "evaluation" if should_run_evaluation else "output"
            )
            workflow["agents"]["latexifier"]["data"]["input"] = (
                uploaded_agent_payload
                if has_uploaded_agent_input and resume_from_agent == "latexifier" and uploaded_agent_payload
                else latex_input_payload
            )
            touch_workflow(workflow)

            start_time = time.time()
            latex_output: LaTeXOutput = await run_llm_stage(
                agents["latexifier"],
                latex_input,
                run_config=run_config,
                max_turns=_max_turns("latexifier"),
                stage_label="latexifier",
                emit_token=emit_token,
            )
            try:
                (
                    latex_output,
                    latex_filename,
                    latex_path,
                    pdf_filename,
                    pdf_path,
                ) = await to_thread(
                    materialize_latex_output,
                    latex_output,
                    outputs_dir,
                    shared_runtime,
                )
            except Exception as exc:
                # The shared container may have expired during the long pre-latex
                # stages. Try ONE recreate (same file_ids), re-bootstrap the LaTeX
                # toolchain, rebuild the agents against the new container, and
                # retry compilation once before giving up.
                if not _is_container_expired_error(exc):
                    raise
                logger.warning(
                    "Workflow %s latexifier hit a container error (%s); "
                    "recreating the container and retrying once.",
                    workflow_id,
                    exc,
                )
                new_container = await to_thread(
                    client.containers.create,
                    name=(
                        f"isomorphic-workflow-{workflow_id}-"
                        f"{output_folder_label.replace('/', '-')}-recreate"
                    ),
                    expires_after={
                        "anchor": "last_active_at",
                        "minutes": container_expires_minutes(),
                    },
                    file_ids=file_ids,
                )
                shared_container_id = new_container.id
                workflow["container"]["id"] = shared_container_id
                touch_workflow(workflow)
                shared_runtime = SharedWorkflowRuntime(
                    client=client,
                    container_id=shared_container_id,
                    output_dir=outputs_dir,
                    allowed_domains=allowed_domains_from_env(),
                )
                await to_thread(shared_runtime.bootstrap)
                agents = create_agents(
                    file_ids=file_ids,
                    container_id=shared_container_id,
                    latex_compile_tool=shared_runtime.build_compile_latex_tool(),
                )
                latex_output = await run_llm_stage(
                    agents["latexifier"],
                    latex_input,
                    run_config=run_config,
                    max_turns=_max_turns("latexifier"),
                    stage_label="latexifier",
                    emit_token=emit_token,
                )
                (
                    latex_output,
                    latex_filename,
                    latex_path,
                    pdf_filename,
                    pdf_path,
                ) = await to_thread(
                    materialize_latex_output,
                    latex_output,
                    outputs_dir,
                    shared_runtime,
                )
                logger.info(
                    "Workflow %s recovered from container expiry (new id=%s).",
                    workflow_id,
                    shared_container_id,
                )
            elapsed = time.time() - start_time

            finalize_agent_run(
                workflow=workflow,
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_metadata_records=agent_metadata_records,
                touch_workflow=touch_workflow,
                agent_key="latexifier",
                agent_name=AGENT_DISPLAY_NAMES["latexifier"],
                elapsed=elapsed,
                input_payload=latex_input,
                output_payload=latex_output,
                metadata={
                    "model": agents["latexifier"].model,
                    "reasoning_level": agent_reasoning_level(agents["latexifier"]),
                    "execution_time_seconds": elapsed,
                    "received_from_node": "generator",
                    "sent_to_node": "evaluation",
                    "input_summary": f"{len(latex_input)} message(s)",
                    "filename": latex_filename,
                    "pdf_filename": pdf_filename,
                },
            )
        else:
            if is_input_to_agent_run:
                latex_output = coerce_latex_from_agent_input(uploaded_agent_payload)
                latex_output, latex_filename, latex_path, pdf_filename, pdf_path = await to_thread(
                    materialize_latex_output,
                    latex_output,
                    outputs_dir,
                    shared_runtime,
                )
                mark_provided_agent_state(
                    workflow,
                    agent_key="latexifier",
                    agent_output=latex_output,
                    input_filename=agent_input_filename,
                    start_agent=resume_from_agent,
                )
                touch_workflow(workflow)
                agent_metadata_records.append(
                    record_provided_agent_artifact(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="latexifier",
                        agent_output=latex_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                )
            elif resume_source_dir is None:
                raise ValueError("resume_source_dir is required for cached latex output")
            else:
                latex_output = load_resume_agent_output(resume_source_dir, "latexifier")
                mark_reused_agent_state(
                    workflow,
                    agent_key="latexifier",
                    agent_output=latex_output,
                    source_folder=resume_source_label,
                )
                touch_workflow(workflow)
                agent_metadata_records.append(
                    copy_or_record_reused_agent_artifact(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="latexifier",
                        agent_output=latex_output,
                        resume_source_dir=resume_source_dir,
                        resume_source_label=resume_source_label,
                    )
                )
                latex_filename = safe_filename(
                    latex_output.filename or "isomorphic_questions.tex",
                    fallback="isomorphic_questions.tex",
                    allowed_suffixes=LATEX_SUFFIXES,
                )
                latex_path = ensure_child_path(outputs_dir, latex_filename)
                await to_thread(
                    latex_path.write_text,
                    latex_output.latex_content,
                    encoding="utf-8",
                )
                pdf_filename = safe_filename(
                    latex_output.pdf_filename or "isomorphic_questions.pdf",
                    fallback="isomorphic_questions.pdf",
                    allowed_suffixes=PDF_SUFFIXES,
                )
                pdf_path = ensure_child_path(outputs_dir, pdf_filename)
                source_pdf_path = ensure_child_path(resume_source_dir, pdf_filename)
                if source_pdf_path.exists():
                    await to_thread(shutil.copy2, source_pdf_path, pdf_path)

        if stop_after_agent == "latexifier":
            workflow["status"] = "completed"
            touch_workflow(workflow)
            return _build_partial_result(
                normalizer_output=normalizer_output,
                extracter_output=extracter_output,
                verifier_output=verifier_output,
                generator_output=generator_output,
                latex_output=latex_output,
            )

        # ---- Evaluation ----
        input_name_map: dict[str, str] = {}
        if should_run_evaluation:
            workflow["agents"]["evaluation"]["status"] = "thinking"
            workflow["agents"]["evaluation"]["data"]["startTime"] = datetime.now().isoformat()
            workflow["agents"]["evaluation"]["data"]["model"] = load_rubric_model()
            workflow["agents"]["evaluation"]["data"]["reasoningLevel"] = _eval_reasoning()
            touch_workflow(workflow)

            evaluation_stage_start = time.time()
            contract_report = validate_generator_output(
                verifier_output.questions,
                generator_output,
                normalizer_output.new_questions_per_question,
            ).model_dump()
            evaluation_final_result = build_evaluation_final_result(
                normalizer_output,
                extracter_output,
                verifier_output,
                generator_output,
            )
            evaluation_input = {
                "task": "evaluate_generated_output_artifacts",
                "workflow_id": workflow_id,
                "latex_file": latex_filename,
                "pdf_file": pdf_filename,
                "latex_output": latex_output.model_dump(),
                "final_result": evaluation_final_result,
                "rubric": load_rubric_payload(),
            }
            if has_uploaded_agent_input and resume_from_agent == "evaluation":
                evaluation_input = uploaded_agent_input
            workflow["agents"]["evaluation"]["data"]["receivedFrom"] = "latexifier"
            workflow["agents"]["evaluation"]["data"]["sentTo"] = "output"
            workflow["agents"]["evaluation"]["data"]["input"] = (
                uploaded_agent_payload
                if has_uploaded_agent_input and resume_from_agent == "evaluation" and uploaded_agent_payload
                else f"Evaluate generated questions for workflow {workflow_id}"
            )
            workflow["agents"]["evaluation"]["data"]["output"] = "Running evaluation..."
            touch_workflow(workflow)

            # Copy source PDFs into the output folder before evaluation so the
            # report's run_inputs can reference downloadable relative paths.
            input_files_dir = outputs_dir / "input_files"

            def _copy_source_pdf(src: Path) -> str:
                input_files_dir.mkdir(parents=True, exist_ok=True)
                dest_name = safe_filename(
                    src.name, fallback="input.pdf",
                    allowed_suffixes={".pdf", ".txt", ".docx"},
                )
                dest = input_files_dir / dest_name
                if not dest.exists():
                    shutil.copy2(src, dest)
                return dest_name

            for raw_fp in file_paths:
                src = Path(raw_fp)
                if not src.is_file():
                    continue
                try:
                    dest_name = await to_thread(_copy_source_pdf, src)
                    input_name_map[src.name] = f"input_files/{dest_name}"
                except Exception:
                    logger.exception("Failed to copy source PDF %s", src)

            evaluation_report = await asyncio.to_thread(
                run_openai_output_evaluation,
                client=client,
                final_result=evaluation_final_result,
                outputs_dir=outputs_dir,
                source_files=file_paths,
                contract_report=contract_report,
            )
            evaluation_elapsed = time.time() - evaluation_stage_start

            # Update run_inputs names to downloadable relative paths and re-save.
            if input_name_map and isinstance(evaluation_report.get("run_inputs"), list):
                for entry in evaluation_report["run_inputs"]:
                    bare = Path(entry.get("name", "")).name
                    if bare in input_name_map:
                        entry["name"] = input_name_map[bare]
                eval_report_path_on_disk = (
                    outputs_dir / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_REPORT_FILENAME
                )
                if eval_report_path_on_disk.exists():
                    await to_thread(
                        eval_report_path_on_disk.write_text,
                        json.dumps(evaluation_report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

            workflow["agents"]["evaluation"]["status"] = "completed"
            workflow["agents"]["evaluation"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["evaluation"]["data"]["executionTime"] = evaluation_elapsed
            workflow["agents"]["evaluation"]["data"]["output"] = evaluation_report
            touch_workflow(workflow)
            agent_metadata_records.append(
                write_agent_artifacts(
                    outputs_dir=outputs_dir,
                    workflow_id=workflow_id,
                    agent_key="evaluation",
                    agent_name="Evaluator",
                    input_payload=evaluation_input,
                    output_payload=evaluation_report,
                    metadata={
                        "execution_time_seconds": evaluation_elapsed,
                        "reasoning_level": _eval_reasoning(),
                        "received_from_node": "latexifier",
                        "sent_to_node": "output",
                        "input_summary": (
                            f"Evaluate generated output artifacts for workflow {workflow_id}"
                        ),
                    },
                )
            )
        elif should_run_agent("evaluation", resume_from_agent):
            evaluation_report = None
            workflow["agents"]["evaluation"]["status"] = "inactive"
            workflow["agents"]["evaluation"]["data"]["receivedFrom"] = "latexifier"
            workflow["agents"]["evaluation"]["data"]["sentTo"] = "output"
            workflow["agents"]["evaluation"]["data"]["model"] = load_rubric_model()
            workflow["agents"]["evaluation"]["data"]["reasoningLevel"] = _eval_reasoning()
            workflow["agents"]["evaluation"]["data"]["input"] = "Evaluation disabled for this workflow"
            workflow["agents"]["evaluation"]["data"]["output"] = "Skipped because Run Evaluations is off."
            touch_workflow(workflow)
        else:
            if resume_source_dir is None:
                raise ValueError("resume_source_dir is required for cached evaluation output")
            evaluation_report = load_agent_output_payload(resume_source_dir, "evaluation")
            if evaluation_report is None:
                evaluation_report = {}
            mark_reused_agent_state(
                workflow,
                agent_key="evaluation",
                agent_output=evaluation_report,
                source_folder=resume_source_label,
            )
            touch_workflow(workflow)
            agent_metadata_records.append(
                copy_or_record_reused_agent_artifact(
                    outputs_dir=outputs_dir,
                    workflow_id=workflow_id,
                    agent_key="evaluation",
                    agent_output=evaluation_report,
                    resume_source_dir=resume_source_dir,
                    resume_source_label=resume_source_label,
                )
            )

        # ---- Output finalization ----
        workflow["agents"]["output"]["status"] = "thinking"
        workflow["agents"]["output"]["data"]["startTime"] = datetime.now().isoformat()
        output_received_from = "latexifier" if evaluation_was_skipped else "evaluation"
        workflow["agents"]["output"]["data"]["receivedFrom"] = output_received_from
        workflow["agents"]["output"]["data"]["sentTo"] = "user"
        workflow["agents"]["output"]["data"]["model"] = "Workflow Runtime"
        workflow["agents"]["output"]["data"]["input"] = (
            f"Finalize local output files in outputs/{output_folder_label}"
        )
        workflow["agents"]["output"]["data"]["output"] = "Writing local artifacts..."
        touch_workflow(workflow)

        output_stage_start = time.time()

        final_result = WorkflowResult(
            config=normalizer_output,
            source_questions=extracter_output.questions,
            verified_questions=verifier_output.questions,
            generated_questions=generator_output.isomorphic_questions,
            latex_file=latex_filename,
            pdf_file=pdf_filename,
            evaluation=evaluation_report,
        )

        result_path = outputs_dir / "final_result.json"
        await to_thread(
            result_path.write_text,
            final_result.model_dump_json(indent=2),
            encoding="utf-8",
        )

        evaluation_outputs_dir = outputs_dir / EVALUATION_OUTPUTS_DIRNAME
        eval_report_path = evaluation_outputs_dir / EVALUATION_REPORT_FILENAME
        eval_dataset_path = evaluation_outputs_dir / EVALUATION_DATASET_FILENAME
        eval_items_path = evaluation_outputs_dir / EVALUATION_ITEMS_FILENAME
        eval_spreadsheet_path = evaluation_outputs_dir / EVALUATION_SPREADSHEET_FILENAME
        eval_rubric_path = evaluation_outputs_dir / EVALUATION_RUBRIC_FILENAME
        files_metadata = [
            file_metadata("final_result.json", result_path),
            file_metadata(latex_filename, latex_path),
        ]
        if pdf_path.exists():
            files_metadata.append(file_metadata(pdf_filename, pdf_path))
        if eval_report_path.exists():
            files_metadata.append(file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_REPORT_FILENAME}", eval_report_path))
        if eval_dataset_path.exists():
            files_metadata.append(file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_DATASET_FILENAME}", eval_dataset_path))
        if eval_items_path.exists():
            files_metadata.append(file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_ITEMS_FILENAME}", eval_items_path))
        if eval_spreadsheet_path.exists():
            files_metadata.append(file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_SPREADSHEET_FILENAME}", eval_spreadsheet_path))
        if eval_rubric_path.exists():
            files_metadata.append(file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_RUBRIC_FILENAME}", eval_rubric_path))
        for relative_name in input_name_map.values():
            dest = outputs_dir / relative_name
            if dest.exists():
                files_metadata.append(file_metadata(relative_name, dest))

        output_input_artifact = "agent_outputs/output/agent_input.jsonl"
        output_output_artifact = "agent_outputs/output/agent_output.jsonl"
        agent_artifact_file_names: list[str] = []
        for record in agent_metadata_records:
            for artifact_key in ("input_artifact", "output_artifact"):
                artifact_name = record.get(artifact_key)
                if artifact_name:
                    agent_artifact_file_names.append(str(artifact_name))
        agent_artifact_file_names.extend([output_input_artifact, output_output_artifact])
        output_file_names = list(
            dict.fromkeys(
                [item["name"] for item in files_metadata]
                + agent_artifact_file_names
                + [AGENT_METADATA_RELATIVE_PATH]
            )
        )

        output_payload: dict[str, Any] = {
            "agent_name": "Output",
            "execution_time_seconds": None,
            "received_from_node": output_received_from,
            "sent_to_node": "user",
            "input_summary": f"Finalize output files for workflow {workflow_id}",
            "output": {
                "output_folder": output_folder_label,
                "files": output_file_names,
            },
        }

        output_elapsed = time.time() - output_stage_start
        output_payload["execution_time_seconds"] = output_elapsed
        output_metadata_record = write_agent_artifacts(
            outputs_dir=outputs_dir,
            workflow_id=workflow_id,
            agent_key="output",
            agent_name="Output",
            input_payload={
                "task": "finalize_output_files",
                "workflow_id": workflow_id,
                "output_folder": output_folder_label,
            },
            output_payload=output_payload["output"],
            metadata={
                "execution_time_seconds": output_elapsed,
                "received_from_node": output_received_from,
                "sent_to_node": "user",
                "input_summary": f"Finalize output files for workflow {workflow_id}",
                "output_folder": output_folder_label,
                "files": output_payload["output"]["files"],
            },
        )
        agent_metadata_records.append(output_metadata_record)

        metadata_path = write_workflow_metadata(
            outputs_dir=outputs_dir,
            workflow_id=workflow_id,
            agents=agent_metadata_records,
            output_folder=output_folder_label,
            workflow_type=workflow_type,
            source_files=file_paths,
            resumed_from=(
                {
                    "input_file": agent_input_filename,
                    "start_agent": resume_from_agent,
                }
                if is_input_to_agent_run
                else
                {
                    "output_folder": resume_source_label,
                    "start_agent": resume_from_agent,
                }
                if resume_source_dir
                else None
            ),
        )

        for record in agent_metadata_records:
            for artifact_key in ("input_artifact", "output_artifact"):
                artifact_name = record.get(artifact_key)
                if not artifact_name:
                    continue
                artifact_path = outputs_dir / str(artifact_name)
                if artifact_path.exists():
                    files_metadata.append(file_metadata(str(artifact_name), artifact_path))
        files_metadata.append(file_metadata(AGENT_METADATA_RELATIVE_PATH, metadata_path))

        workflow["files"] = files_metadata
        workflow["agents"]["output"]["status"] = "completed"
        workflow["agents"]["output"]["data"]["endTime"] = datetime.now().isoformat()
        workflow["agents"]["output"]["data"]["executionTime"] = output_elapsed
        workflow["agents"]["output"]["data"]["output"] = (
            f"Saved {len(files_metadata)} file(s) to outputs/{output_folder_label}"
        )
        workflow["result"] = final_result.model_dump()
        workflow["status"] = "completed"
        touch_workflow(workflow)

        logger.info("Workflow %s completed successfully", workflow_id)

        return final_result

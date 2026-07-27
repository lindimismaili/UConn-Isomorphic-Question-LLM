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
import os
import shutil
import time
from dataclasses import dataclass, field
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
from agents.run import ToolExecutionConfig
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
from ..container import (
    CompileAttemptBudget,
    SharedWorkflowRuntime,
    allowed_domains_from_env,
    container_network_policy,
    max_generated_artifact_bytes,
)
from ..cost_controls import (
    generator_style_review_enabled,
    grader_max_completion_tokens,
    max_evaluation_items,
    max_generated_questions,
    max_source_questions,
    max_workflow_agent_requests,
    max_workflow_agent_tokens,
    require_within_limit,
)
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
from ..provider_timeouts import (
    agent_stage_timeout_seconds,
    await_cancellation_safe_blocking_call,
    await_with_deadline,
    container_task_timeout_seconds,
    drain_task_outcome,
    evaluation_timeout_seconds,
)
from ..registry import agent_entry as _agent_entry
from ..registry import canonical_theme as _canonical_theme
from ..registry import default_from_agent as _registry_default_from_agent
from ..registry import evaluation_agent as _evaluation_agent
from ..registry import guardrail_content_node as _guardrail_content_node
from ..registry import guardrail_input_node as _guardrail_input_node
from ..registry import stoppable_agents as _registry_stoppable_agents
from ..safety import (
    LATEX_SUFFIXES,
    PDF_SUFFIXES,
    SafeConfigurationError,
    SanitizedProviderError,
    ensure_child_path,
    safe_exception_category,
    safe_filename,
    stable_short_hash,
    unique_child_name,
)
from ..schemas import (
    ExtracterOutput,
    GeneratorOutput,
    LaTeXOutput,
    NormalizerOutput,
    SourceQuestionItem,
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
    """Heuristic: does this error indicate the active container expired/vanished?

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
RecordUsage = Callable[[str, Any], None]
StageMaxTurns = Callable[[str], int]
EVALUATION_SUCCESS_STATUSES = frozenset({"complete", "completed"})


def _planned_generated_question_count(
    questions: list[SourceQuestionItem],
    per_source: int,
) -> int:
    """Count generator outputs using the figure-placeholder contract."""
    requested = max(1, int(per_source or 1))
    return sum(1 if question.has_figure else requested for question in questions)


def _tracing_disabled() -> bool:
    """Default SDK tracing off; honor its standard explicit environment flag."""
    raw = os.getenv("OPENAI_AGENTS_DISABLE_TRACING")
    if raw is None:
        return True
    normalized = raw.strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    return True


def _stage_is_planned(
    agent_key: str,
    *,
    resume_from_agent: str,
    stop_after_agent: Optional[str],
) -> bool:
    """Return whether a stage can be reached by this resume/stop plan."""
    if not should_run_agent(agent_key, resume_from_agent):
        return False
    if stop_after_agent is None:
        return True
    return WORKFLOW_AGENT_SEQUENCE.index(agent_key) <= WORKFLOW_AGENT_SEQUENCE.index(
        stop_after_agent
    )


async def _cleanup_openai_resources(
    *,
    client: OpenAI,
    to_thread: ToThread,
    file_ids: list[str],
    container_ids: list[str],
) -> None:
    """Best-effort cleanup for transient provider resources owned by one run."""
    cleanup_cancelled: asyncio.CancelledError | None = None
    delete_container = getattr(getattr(client, "containers", None), "delete", None)
    for container_id in reversed(list(dict.fromkeys(container_ids))):
        try:
            await _retire_owned_provider_resource(
                to_thread=to_thread,
                delete=delete_container,
                resource_id=container_id,
                owned_ids=container_ids,
                resource_kind="container",
            )
        except asyncio.CancelledError as exc:
            cleanup_cancelled = exc

    delete_file = getattr(getattr(client, "files", None), "delete", None)
    for file_id in reversed(list(dict.fromkeys(file_ids))):
        try:
            await _retire_owned_provider_resource(
                to_thread=to_thread,
                delete=delete_file,
                resource_id=file_id,
                owned_ids=file_ids,
                resource_kind="file",
            )
        except asyncio.CancelledError as exc:
            cleanup_cancelled = exc
    if cleanup_cancelled is not None:
        raise cleanup_cancelled


def _record_owned_provider_id(owned_ids: list[str], result: Any) -> str:
    """Extract and record a provider resource id from a completed create call."""
    resource_id = str(getattr(result, "id", result) or "")
    if not resource_id:
        raise RuntimeError("Provider resource creation returned no id")
    owned_ids.append(resource_id)
    return resource_id


async def _run_owned_provider_create(
    *,
    to_thread: ToThread,
    create: Callable[..., Any],
    owned_ids: list[str],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Run a blocking provider create without losing its id to cancellation."""
    create_task = asyncio.create_task(
        to_thread(create, *args, **(kwargs or {}))
    )
    try:
        result = await asyncio.shield(create_task)
    except asyncio.CancelledError as cancellation:
        outcome = await drain_task_outcome(create_task)
        if not isinstance(outcome, BaseException):
            _record_owned_provider_id(owned_ids, outcome)
        raise cancellation
    _record_owned_provider_id(owned_ids, result)
    return result


async def _attach_provider_file_to_container(
    *,
    client: OpenAI,
    to_thread: ToThread,
    container_id: str,
    file_id: str,
) -> None:
    """Attach one owned provider file and drain the call before cancellation."""
    create = getattr(
        getattr(getattr(client, "containers", None), "files", None),
        "create",
        None,
    )
    if not callable(create):
        raise RuntimeError(
            "OpenAI SDK does not support isolated container file attachment; "
            "upgrade openai to a version with the Containers Files API"
        )
    attach_task = asyncio.create_task(
        to_thread(create, container_id, file_id=file_id)
    )
    try:
        await asyncio.shield(attach_task)
    except asyncio.CancelledError as cancellation:
        await drain_task_outcome(attach_task)
        raise cancellation


async def _retire_owned_provider_resource(
    *,
    to_thread: ToThread,
    delete: Callable[..., Any] | None,
    resource_id: str,
    owned_ids: list[str],
    resource_kind: str,
) -> bool:
    """Delete one owned resource and remove it from its ledger on success."""
    if not resource_id:
        return True
    if not callable(delete):
        logger.warning(
            "Could not delete temporary OpenAI %s %s (cleanup API unavailable)",
            resource_kind,
            resource_id,
        )
        return False
    delete_task = asyncio.create_task(to_thread(delete, resource_id))
    try:
        await asyncio.shield(delete_task)
    except asyncio.CancelledError as cancellation:
        outcome = await drain_task_outcome(delete_task)
        if (
            isinstance(outcome, BaseException)
            and getattr(outcome, "status_code", None) != 404
        ):
            logger.warning(
                "Could not delete temporary OpenAI %s %s (%s)",
                resource_kind,
                resource_id,
                safe_exception_category(outcome),
            )
        else:
            owned_ids[:] = [item for item in owned_ids if item != resource_id]
        raise cancellation
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            owned_ids[:] = [item for item in owned_ids if item != resource_id]
            return True
        logger.warning(
            "Could not delete temporary OpenAI %s %s (%s)",
            resource_kind,
            resource_id,
            safe_exception_category(exc),
        )
        return False
    owned_ids[:] = [item for item in owned_ids if item != resource_id]
    return True


@dataclass
class _WorkflowContainerContext:
    """One provider container and the local tasks that keep it usable."""

    role: str
    container_id: str
    runtime: SharedWorkflowRuntime
    agents: dict[str, Any]
    heartbeat_task: asyncio.Task[Any]
    bootstrap_tasks: list[asyncio.Task[Any]] = field(default_factory=list)


class _WorkflowContainerManager:
    """Own the extraction/downstream container trust boundary for one workflow."""

    def __init__(
        self,
        *,
        client: OpenAI,
        workflow_id: str,
        workflow: dict[str, Any],
        outputs_dir: Path,
        output_folder_label: str,
        allowed_domains: list[str],
        touch_workflow: TouchWorkflow,
        to_thread: ToThread,
        owned_container_ids: list[str],
    ) -> None:
        self.client = client
        self.workflow_id = workflow_id
        self.workflow = workflow
        self.outputs_dir = outputs_dir
        self.output_folder_label = output_folder_label
        self.allowed_domains = allowed_domains
        self.touch_workflow = touch_workflow
        self.to_thread = to_thread
        self.owned_container_ids = owned_container_ids
        self.compile_attempt_budget = CompileAttemptBudget(
            on_claim=self._record_compile_attempt
        )
        self.current: _WorkflowContainerContext | None = None
        self._contexts: list[_WorkflowContainerContext] = []

    def _record_compile_attempt(self, attempts: int) -> None:
        usage = self.workflow.setdefault("usage", {})
        usage["latexCompileAttempts"] = attempts
        usage.setdefault(
            "latexCompileAttemptLimit",
            self.compile_attempt_budget.max_attempts,
        )
        self.touch_workflow(self.workflow)

    def _record_shell_call(self) -> None:
        usage = self.workflow.setdefault("usage", {})
        calls = int(usage.get("directShellCalls", 0)) + 1
        limit = int(
            usage.get(
                "directShellCallLimit",
                self.compile_attempt_budget.max_attempts + 1,
            )
        )
        usage["directShellCalls"] = calls
        usage["directShellCallLimit"] = limit
        self.touch_workflow(self.workflow)
        if calls > limit:
            raise ValueError(
                "Workflow direct shell-call usage exceeded its structural limit."
            )

    async def _heartbeat(self, container_id: str, role: str) -> None:
        interval = max(
            30,
            min(container_expires_minutes() * 60 // 2, 300),
        )
        while True:
            await asyncio.sleep(interval)
            retrieve_task = asyncio.create_task(
                self.to_thread(
                    self.client.containers.retrieve,
                    container_id,
                )
            )
            try:
                await asyncio.shield(retrieve_task)
            except asyncio.CancelledError as cancellation:
                await drain_task_outcome(retrieve_task)
                raise cancellation
            except Exception as exc:
                logger.warning(
                    "Workflow %s %s container heartbeat failed (%s)",
                    self.workflow_id,
                    role,
                    safe_exception_category(exc),
                )

    async def provision(
        self,
        *,
        role: str,
        file_ids: list[str],
    ) -> _WorkflowContainerContext:
        """Create and track one explicit container, never an SDK auto-container."""
        if self.current is not None:
            raise RuntimeError("A workflow container must be retired before replacement")
        if not hasattr(self.client, "containers"):
            raise RuntimeError(
                "OpenAI SDK does not support containers; upgrade openai to >=2.0.0"
            )

        state = self.workflow["container"]
        state["status"] = "creating"
        state["phase"] = role
        state["id"] = ""
        state["allowedDomains"] = self.allowed_domains
        self.touch_workflow(self.workflow)
        logger.info(
            "Workflow %s creating %s OpenAI container",
            self.workflow_id,
            role,
        )

        suffix = role.replace("_", "-")
        try:
            container = await _run_owned_provider_create(
                to_thread=self.to_thread,
                create=self.client.containers.create,
                owned_ids=self.owned_container_ids,
                kwargs={
                    "name": (
                        f"isomorphic-workflow-{self.workflow_id}-"
                        f"{self.output_folder_label.replace('/', '-')}-{suffix}"
                    ),
                    "expires_after": {
                        "anchor": "last_active_at",
                        "minutes": container_expires_minutes(),
                    },
                    "file_ids": list(file_ids),
                    "network_policy": container_network_policy(self.allowed_domains),
                },
            )
        except Exception as exc:
            if (
                getattr(exc, "code", None) == "invalid_value"
                and getattr(exc, "param", None) == "network_policy.type"
                and "organization policy disables container network access" in str(exc)
            ):
                raise SafeConfigurationError(
                    category="ContainerNetworkAccessDisabled",
                    message=(
                        "Document extraction cannot start because this OpenAI organization "
                        "disables Code Interpreter container networking. An organization admin "
                        "must allow the app's configured container domains before retrying."
                    ),
                ) from None
            raise
        container_id = str(container.id)
        runtime = SharedWorkflowRuntime(
            client=self.client,
            container_id=container_id,
            output_dir=self.outputs_dir,
            allowed_domains=self.allowed_domains,
            compile_attempt_budget=self.compile_attempt_budget,
            record_shell_call=self._record_shell_call,
        )
        extraction_scope = role == "extraction"
        agents = create_agents(
            container_id=container_id,
            latex_compile_tool=(
                None
                if extraction_scope
                else runtime.build_compile_latex_tool()
            ),
            agent_keys=(
                {"extracter"}
                if extraction_scope
                else {"verifier", "generator", "latexifier"}
            ),
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(container_id, role),
            name=f"workflow-{self.workflow_id}-{role}-container-heartbeat",
        )
        context = _WorkflowContainerContext(
            role=role,
            container_id=container_id,
            runtime=runtime,
            agents=agents,
            heartbeat_task=heartbeat_task,
        )
        self._contexts.append(context)
        self.current = context

        state["status"] = "active"
        state["phase"] = role
        state["id"] = container_id
        self.touch_workflow(self.workflow)
        logger.info(
            "Workflow %s %s container created (id=%s)",
            self.workflow_id,
            role,
            container_id,
        )
        return context

    def start_document_bootstrap(self) -> asyncio.Task[Any]:
        """Start the extraction dependency bootstrap only on a cache miss."""
        context = self.current
        if context is None or context.role != "extraction":
            raise RuntimeError("Document bootstrap requires the isolated extraction container")
        if context.bootstrap_tasks:
            return context.bootstrap_tasks[0]

        task = asyncio.create_task(
            self.to_thread(context.runtime.bootstrap),
            name=f"workflow-{self.workflow_id}-document-bootstrap",
        )
        task.add_done_callback(
            lambda done: _log_task_exception(
                done,
                f"Workflow {self.workflow_id} document runtime bootstrap",
            )
        )
        context.bootstrap_tasks.append(task)
        return task

    async def await_document_bootstrap(self) -> Any:
        """Drain the bootstrap worker before propagating cancellation."""
        task = self.start_document_bootstrap()
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            await drain_task_outcome(task)
            raise cancellation

    async def retire_active(self, *, next_status: str = "retired") -> bool:
        """Drain local tasks, detach state, and delete the active container."""
        context = self.current
        if context is None:
            return True

        for task in context.bootstrap_tasks:
            await drain_task_outcome(task)
        context.heartbeat_task.cancel()
        await drain_task_outcome(context.heartbeat_task)

        self.current = None
        state = self.workflow["container"]
        state["status"] = "retiring"
        state["phase"] = context.role
        state["id"] = ""
        self.touch_workflow(self.workflow)
        try:
            retired = await _retire_owned_provider_resource(
                to_thread=self.to_thread,
                delete=getattr(getattr(self.client, "containers", None), "delete", None),
                resource_id=context.container_id,
                owned_ids=self.owned_container_ids,
                resource_kind="container",
            )
        finally:
            state["status"] = next_status
            state["id"] = ""
            self.touch_workflow(self.workflow)
        return retired

    async def close_background_tasks(self) -> None:
        """Drain all bootstraps and stop all heartbeats before outer cleanup."""
        bootstrap_tasks = [
            task
            for context in self._contexts
            for task in context.bootstrap_tasks
        ]
        for task in bootstrap_tasks:
            await drain_task_outcome(task)
        heartbeat_tasks = [context.heartbeat_task for context in self._contexts]
        for task in heartbeat_tasks:
            task.cancel()
        for task in heartbeat_tasks:
            await drain_task_outcome(task)


def _archive_source_files(
    file_paths: list[str],
    outputs_dir: Path,
) -> tuple[dict[str, list[str]], list[str]]:
    """Snapshot source files with their real suffixes and collision-safe names."""
    input_files_dir = outputs_dir / "input_files"
    name_map: dict[str, list[str]] = {}
    relative_names: list[str] = []
    used_names: set[str] = set()

    for raw_path in file_paths:
        source = Path(raw_path)
        if not source.is_file():
            continue
        input_files_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_filename(source.name, fallback="input.bin")
        destination_name = unique_child_name(
            input_files_dir,
            safe_name,
            used_names,
        )
        shutil.copy2(source, input_files_dir / destination_name)
        relative_name = f"input_files/{destination_name}"
        name_map.setdefault(source.name, []).append(relative_name)
        relative_names.append(relative_name)

    return name_map, relative_names


async def run_llm_stage(
    agent: Any,
    input: Any,
    *,
    run_config: RunConfig,
    max_turns: int,
    stage_label: str,
    emit_token: Optional[EmitToken] = None,
    record_usage: Optional[RecordUsage] = None,
    timeout_seconds: float | None = None,
) -> Any:
    """Run one LLM stage and return ``final_output``.

    Behavior is identical to a bare ``await Runner.run(...)`` when ``emit_token``
    is None (the mock-compatible non-streaming path). When ``emit_token`` is set,
    the run is streamed and each output-text delta is forwarded to the callback;
    a callback failure never breaks the run. SDK failures are normalized into a
    ``ValueError`` carrying the stage label so callers surface a clean error.
    """
    async def _run() -> Any:
        if emit_token is None:
            result = await Runner.run(
                agent, input=input, run_config=run_config, max_turns=max_turns
            )
            if record_usage is not None:
                context = getattr(result, "context_wrapper", None)
                record_usage(stage_label, getattr(context, "usage", None))
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
        if record_usage is not None:
            context = getattr(streamed, "context_wrapper", None)
            record_usage(stage_label, getattr(context, "usage", None))
        return streamed.final_output

    deadline = (
        agent_stage_timeout_seconds()
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        return await await_with_deadline(
            _run(),
            operation=f"{stage_label} stage",
            timeout_seconds=deadline,
        )
    except (MaxTurnsExceeded, ModelBehaviorError, ModelRefusalError) as exc:
        raise SanitizedProviderError(
            operation=f"{stage_label} stage",
            cause=exc,
        ) from None


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

    Used for the document bootstrap task, which is awaited before a real
    Extracter call. If the run errors before that stage, this callback still
    consumes the exception and avoids an "exception was never retrieved" warning.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s failed (%s)", label, safe_exception_category(exc))


def materialize_latex_output(
    latex_output: LaTeXOutput,
    outputs_dir: Path,
    shared_runtime: Optional[SharedWorkflowRuntime],
) -> tuple[LaTeXOutput, str, Path, str, Path]:
    """Write LaTeX safely, compile when a runtime exists, and return canonical paths."""
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
    if not pdf_path.exists() and shared_runtime is not None:
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


async def materialize_latex_output_async(
    latex_output: LaTeXOutput,
    outputs_dir: Path,
    shared_runtime: Optional[SharedWorkflowRuntime],
    *,
    to_thread: ToThread,
) -> tuple[LaTeXOutput, str, Path, str, Path]:
    """Materialize locally, then compile in cancellation-safe provider steps."""
    materialized = await await_cancellation_safe_blocking_call(
        to_thread,
        materialize_latex_output,
        latex_output,
        outputs_dir,
        None,
    )
    (
        updated,
        latex_filename,
        latex_path,
        pdf_filename,
        pdf_path,
    ) = materialized
    if pdf_path.exists() or shared_runtime is None:
        return materialized

    compile_result = await shared_runtime.compile_latex_async(
        updated.latex_content,
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
    updated = updated.model_copy(
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
        "Workflow %s blocked by %s guardrail (category=%s)",
        workflow_id,
        decision.stage,
        decision.category,
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
    run_evaluation: bool = False,
    stop_after_agent: Optional[str] = None,
    emit_token: Optional[EmitToken] = None,
    model_provider: Any = None,
) -> WorkflowResult:
    """Run one workflow and always release its temporary OpenAI resources."""
    resolved_theme = _canonical_theme(theme)
    uploaded_file_ids: list[str] = []
    created_container_ids: list[str] = []
    try:
        return await _run_workflow_async_impl(
            client=client,
            workflow_id=workflow_id,
            workflow=workflow,
            prompt=prompt,
            file_paths=file_paths,
            outputs_dir=outputs_dir,
            output_folder_label=output_folder_label,
            touch_workflow=touch_workflow,
            theme=resolved_theme,
            to_thread=to_thread,
            load_rubric_model=load_rubric_model,
            load_rubric_payload=load_rubric_payload,
            resume_from_agent=resume_from_agent,
            resume_source_dir=resume_source_dir,
            resume_source_label=resume_source_label,
            agent_input_override=agent_input_override,
            agent_input_filename=agent_input_filename,
            input_to_agent_run=input_to_agent_run,
            run_evaluation=run_evaluation,
            stop_after_agent=stop_after_agent,
            emit_token=emit_token,
            model_provider=model_provider,
            uploaded_file_ids=uploaded_file_ids,
            created_container_ids=created_container_ids,
        )
    finally:
        try:
            await _cleanup_openai_resources(
                client=client,
                to_thread=to_thread,
                file_ids=uploaded_file_ids,
                container_ids=created_container_ids,
            )
        finally:
            container_state = workflow.get("container")
            if isinstance(container_state, dict):
                container_state["status"] = (
                    "cleanup_pending"
                    if created_container_ids
                    else "retired"
                )
                container_state["id"] = ""
                touch_workflow(workflow)


async def _run_workflow_async_impl(
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
    run_evaluation: bool,
    stop_after_agent: Optional[str] = None,
    emit_token: Optional[EmitToken] = None,
    model_provider: Any = None,
    uploaded_file_ids: list[str],
    created_container_ids: list[str],
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
    tracing_disabled = _tracing_disabled()
    with trace(
        "Isomorphic Question Generation Workflow",
        disabled=tracing_disabled,
    ):
        if resume_from_agent not in WORKFLOW_AGENT_SEQUENCE:
            raise ValueError(f"Unknown resume_from_agent: {resume_from_agent}")
        if resume_from_agent == _evaluation_agent() and not run_evaluation:
            raise ValueError(
                "Starting from the evaluation stage requires explicit paid evaluation opt-in"
            )
        _stoppable_agents = _registry_stoppable_agents()
        if stop_after_agent is not None and stop_after_agent not in _stoppable_agents:
            raise ValueError(
                f"stop_after_agent must be one of {_stoppable_agents}, got: {stop_after_agent!r}"
            )
        usage_request_limit = max_workflow_agent_requests()
        usage_token_limit = max_workflow_agent_tokens()
        source_question_limit = max_source_questions()
        generated_question_limit = max_generated_questions()
        style_review_enabled = generator_style_review_enabled()
        agent_stage_timeout_seconds()

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

        run_config_kwargs: dict[str, Any] = {
            "tracing_disabled": tracing_disabled,
            "trace_include_sensitive_data": False,
            "tool_execution": ToolExecutionConfig(
                max_function_tool_concurrency=1
            ),
            "trace_metadata": {
                "__trace_source__": "isomorphic-question-generator",
                "timestamp": output_folder_label.replace("/", "_"),
            },
        }
        if model_provider is not None:
            run_config_kwargs["model_provider"] = model_provider
        run_config = RunConfig(**run_config_kwargs)
        usage_summary: dict[str, Any] = {
            "scope": "pipeline_agents_sdk",
            "requests": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "requestLimit": usage_request_limit,
            "tokenLimit": usage_token_limit,
            "tokenLimitEnforcement": "observed_after_each_agent_run",
            "directShellCalls": 0,
            "directShellCallLimit": 4,
            "latexCompileAttempts": 0,
            "latexCompileAttemptLimit": 3,
            "stages": {},
        }
        workflow["usage"] = usage_summary

        def record_usage(stage_label: str, usage: Any) -> None:
            if usage is None:
                return

            def count(name: str) -> int:
                value = getattr(usage, name, 0)
                if isinstance(value, bool) or not isinstance(value, int):
                    return 0
                return max(0, value)

            observed = {
                "requests": count("requests"),
                "inputTokens": count("input_tokens"),
                "outputTokens": count("output_tokens"),
                "totalTokens": count("total_tokens"),
            }
            stage_usage = usage_summary["stages"].setdefault(
                stage_label,
                {
                    "requests": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                },
            )
            for key, value in observed.items():
                usage_summary[key] += value
                stage_usage[key] += value
            touch_workflow(workflow)
            if usage_summary["requests"] > usage_request_limit:
                raise ValueError(
                    "Workflow agent request usage exceeded "
                    f"MAX_WORKFLOW_AGENT_REQUESTS={usage_request_limit}."
                )
            if usage_summary["totalTokens"] > usage_token_limit:
                raise ValueError(
                    "Workflow agent token usage exceeded "
                    f"MAX_WORKFLOW_AGENT_TOKENS={usage_token_limit}."
                )

        def stage_max_turns(agent_key: str) -> int:
            remaining = usage_request_limit - int(usage_summary["requests"])
            if remaining <= 0:
                raise ValueError(
                    "Workflow agent request usage reached "
                    f"MAX_WORKFLOW_AGENT_REQUESTS={usage_request_limit}."
                )
            return min(_max_turns(agent_key), remaining)

        agent_metadata_records: list[dict[str, Any]] = []
        stage_cache = await await_cancellation_safe_blocking_call(
            to_thread,
            StageCache,
        )
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
            run_evaluation
            and _stage_is_planned(
                "evaluation",
                resume_from_agent=resume_from_agent,
                stop_after_agent=stop_after_agent,
            )
        )
        evaluation_was_skipped = (
            not should_run_evaluation
            and _stage_is_planned(
                "evaluation",
                resume_from_agent=resume_from_agent,
                stop_after_agent=stop_after_agent,
            )
        )
        prompt_text = (prompt or "").strip()
        reaches_latexifier = _stage_is_planned(
            "latexifier",
            resume_from_agent=resume_from_agent,
            stop_after_agent=stop_after_agent,
        )
        reaches_extracter = _stage_is_planned(
            "extracter",
            resume_from_agent=resume_from_agent,
            stop_after_agent=stop_after_agent,
        )
        needs_downstream_container = reaches_latexifier or any(
            _stage_is_planned(
                agent_key,
                resume_from_agent=resume_from_agent,
                stop_after_agent=stop_after_agent,
            )
            and bool(_agent_entry(agent_key).get("useCodeInterpreter"))
            for agent_key in WORKFLOW_AGENT_SEQUENCE
            if agent_key != "extracter"
        )
        needs_any_container = reaches_extracter or needs_downstream_container
        container_allowed_domains = (
            allowed_domains_from_env() if needs_any_container else []
        )
        if needs_any_container:
            container_expires_minutes()
            container_task_timeout_seconds()
            max_generated_artifact_bytes()
        if should_run_evaluation:
            max_evaluation_items()
            grader_max_completion_tokens()
            evaluation_timeout_seconds()
            load_rubric_model()
            load_rubric_payload()

        file_ids = uploaded_file_ids
        file_names = [Path(path).name for path in file_paths]
        file_content_hashes: list[str] = []
        file_paths_to_upload = (
            file_paths
            if reaches_extracter
            else []
        )

        # Input guardrails run before any remote upload or container creation.
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
            untrusted_payload=uploaded_agent_payload if has_uploaded_agent_input else None,
            to_thread=to_thread,
            run_config=run_config,
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

        input_name_map, input_relative_names = await to_thread(
            _archive_source_files,
            file_paths,
            outputs_dir,
        )

        # Hash source bytes locally for cache lookup. Provider upload/container
        # creation is deferred until the Extracter actually misses the cache.
        for file_path in file_paths_to_upload:
            path = Path(file_path)
            if path.exists() and stage_cache.enabled:
                file_content_hashes.append(await to_thread(file_content_hash, path))

        container_manager = _WorkflowContainerManager(
            client=client,
            workflow_id=workflow_id,
            workflow=workflow,
            outputs_dir=outputs_dir,
            output_folder_label=output_folder_label,
            allowed_domains=container_allowed_domains,
            touch_workflow=touch_workflow,
            to_thread=to_thread,
            owned_container_ids=created_container_ids,
        )
        # No tool-bearing stage may run before an Extracter cache miss creates
        # its isolated scope or resolved content passes the content guardrail.
        agents = create_agents(
            code_interpreter_enabled=False,
            agent_keys={"normalizer", "extracter"},
        )

        try:
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
                input_name_map=input_name_map,
                input_relative_names=input_relative_names,
                outputs_dir=outputs_dir,
                output_folder_label=output_folder_label,
                touch_workflow=touch_workflow,
                to_thread=to_thread,
                run_config=run_config,
                agents=agents,
                container_manager=container_manager,
                needs_downstream_container=needs_downstream_container,
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
                stop_after_agent=stop_after_agent,
                load_rubric_model=load_rubric_model,
                load_rubric_payload=load_rubric_payload,
                emit_token=emit_token,
                record_usage=record_usage,
                stage_max_turns=stage_max_turns,
                style_review_enabled=style_review_enabled,
                source_question_limit=source_question_limit,
                generated_question_limit=generated_question_limit,
            )
        finally:
            await container_manager.close_background_tasks()


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
    input_name_map: dict[str, list[str]],
    input_relative_names: list[str],
    outputs_dir: Path,
    output_folder_label: str,
    touch_workflow: TouchWorkflow,
    to_thread: ToThread,
    run_config: RunConfig,
    agents: dict[str, Any],
    container_manager: _WorkflowContainerManager,
    needs_downstream_container: bool,
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
    stop_after_agent: Optional[str],
    load_rubric_model: LoadRubricModel,
    load_rubric_payload: LoadRubricPayload,
    emit_token: Optional[EmitToken],
    record_usage: RecordUsage,
    stage_max_turns: StageMaxTurns,
    style_review_enabled: bool,
    source_question_limit: int,
    generated_question_limit: int,
) -> WorkflowResult:
    """Run stages while enforcing the extraction/downstream container boundary.

    The Extracter is the only stage that can see source-file provider objects.
    Its container is retired after the content guardrail, and later tool stages
    use a fresh explicit container without source-file attachments.
    """
    active_context = container_manager.current
    shared_runtime = active_context.runtime if active_context is not None else None
    shared_container_id = (
        active_context.container_id if active_context is not None else ""
    )
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
            max_turns=stage_max_turns("normalizer"),
            stage_label="normalizer",
            emit_token=emit_token,
            record_usage=record_usage,
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
    ran_extracter = should_run_agent("extracter", resume_from_agent)
    extracter_cache_key = ""
    extracter_cache_hit = False
    extracter_input_payload: dict[str, Any] = {}
    extracter_input: Any = None
    extracter_elapsed = 0.0
    extracter_model = ""
    extracter_reasoning = ""
    if ran_extracter:
        workflow["agents"]["extracter"]["status"] = "thinking"
        workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
        touch_workflow(workflow)

        extracter_input_payload = build_extracter_input(file_names, normalizer_output)
        workflow["agents"]["extracter"]["data"]["receivedFrom"] = _agent_flow()["extracter"][0]
        workflow["agents"]["extracter"]["data"]["sentTo"] = _agent_flow()["extracter"][1]
        workflow["agents"]["extracter"]["data"]["input"] = (
            uploaded_agent_payload
            if has_uploaded_agent_input and resume_from_agent == "extracter" and uploaded_agent_payload
            else extracter_input_payload
        )
        touch_workflow(workflow)

        start_time = time.time()
        pre_guardrail_extracter = agents["extracter"]
        uploaded_extracter_input = (
            has_uploaded_agent_input and resume_from_agent == "extracter"
        )
        extracter_cache_key = (
            stage_cache.make_key(
                "extracter",
                extracter_input_payload,
                model=pre_guardrail_extracter.model,
                reasoning=agent_reasoning_level(pre_guardrail_extracter),
                instructions=getattr(pre_guardrail_extracter, "instructions", ""),
                extra="|".join(file_content_hashes),
            )
            if stage_cache.enabled and not uploaded_extracter_input
            else ""
        )
        cached_extracter = (
            await to_thread(stage_cache.lookup, extracter_cache_key)
            if extracter_cache_key
            else None
        )
        extracter_cache_hit = cached_extracter is not None
        if extracter_cache_hit:
            extracter_output: ExtracterOutput = ExtracterOutput.model_validate(cached_extracter)
            extracter_input = build_json_message(extracter_input_payload)
            extracter_model = pre_guardrail_extracter.model
            extracter_reasoning = agent_reasoning_level(pre_guardrail_extracter)
            logger.info("Workflow %s reused cached Extracter output", workflow_id)
        else:
            extraction_context = await container_manager.provision(
                role="extraction",
                file_ids=[],
            )
            agents = extraction_context.agents
            await container_manager.await_document_bootstrap()
            if file_paths:
                logger.info(
                    "Workflow %s uploading %d source file(s) to isolated extraction scope",
                    workflow_id,
                    len(file_paths),
                )
                for file_path in file_paths:
                    path = Path(file_path)
                    if not path.exists():
                        continue
                    provider_file = await _run_owned_provider_create(
                        to_thread=to_thread,
                        create=upload_file_to_openai,
                        owned_ids=file_ids,
                        args=(client, path, logger),
                    )
                    file_id = str(getattr(provider_file, "id", provider_file))
                    await _attach_provider_file_to_container(
                        client=client,
                        to_thread=to_thread,
                        container_id=extraction_context.container_id,
                        file_id=file_id,
                    )
            # The file is now present in the Extracter's explicitly owned
            # Code Interpreter container. Do not also attach it to the
            # Responses input: that duplicates untrusted source material
            # across two provider paths and can be rejected by the current
            # Responses request validation.
            extracter_input = (
                uploaded_agent_input
                if uploaded_extracter_input
                else build_json_message(extracter_input_payload)
            )
            extracter_model = agents["extracter"].model
            extracter_reasoning = agent_reasoning_level(agents["extracter"])
            extracter_output = await run_llm_stage(
                agents["extracter"],
                extracter_input,
                run_config=run_config,
                max_turns=stage_max_turns("extracter"),
                stage_label="extracter",
                emit_token=emit_token,
                record_usage=record_usage,
            )
            if file_ids and not extracter_output.questions:
                retry_extract_payload = build_extracter_retry_input(file_names, normalizer_output)
                retry_extract_input = build_json_message(retry_extract_payload)
                extracter_input = retry_extract_input
                extracter_input_payload = retry_extract_payload
                extracter_output = await run_llm_stage(
                    agents["extracter"],
                    retry_extract_input,
                    run_config=run_config,
                    max_turns=stage_max_turns("extracter"),
                    stage_label="extracter",
                    emit_token=emit_token,
                    record_usage=record_usage,
                )
        extracter_elapsed = time.time() - start_time
        normalize_question_types(extracter_output.questions)
        if file_ids and not extracter_output.questions:
            raise ValueError("Extractor returned zero questions after retry.")
        merge_extracted_metadata_into_config(normalizer_output, extracter_output)
        workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
        logger.info(
            "Extractor metadata received for workflow %s "
            "(title_present=%s, theme_present=%s, questions=%d)",
            workflow_id,
            bool((extracter_output.title or "").strip()),
            bool((extracter_output.theme or "").strip()),
            len(extracter_output.questions),
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

    require_within_limit(
        "Source question",
        len(extracter_output.questions),
        source_question_limit,
        "MAX_SOURCE_QUESTIONS",
    )
    require_within_limit(
        "Planned generated question",
        _planned_generated_question_count(
            extracter_output.questions,
            normalizer_output.new_questions_per_question,
        ),
        generated_question_limit,
        "MAX_GENERATED_QUESTIONS",
    )

    # Re-screen resolved content on fresh, resumed, and uploaded-input runs.
    # A replay may contain a new prompt or a modified structured artifact.
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
        run_config=run_config,
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

    # Accepted structured output is now safe to cache and persist. The
    # untrusted extraction scope must be gone before any downstream tool
    # stage exists.
    if ran_extracter:
        if not extracter_cache_hit and extracter_cache_key:
            await to_thread(
                stage_cache.store,
                extracter_cache_key,
                agent_key="extracter",
                output=extracter_output.model_dump(),
                model=extracter_model,
                reasoning=extracter_reasoning,
            )
        finalize_agent_run(
            workflow=workflow,
            outputs_dir=outputs_dir,
            workflow_id=workflow_id,
            agent_metadata_records=agent_metadata_records,
            touch_workflow=touch_workflow,
            agent_key="extracter",
            agent_name=AGENT_DISPLAY_NAMES["extracter"],
            elapsed=extracter_elapsed,
            input_payload=extracter_input,
            output_payload=extracter_output,
            metadata={
                "model": extracter_model,
                "reasoning_level": extracter_reasoning,
                "execution_time_seconds": extracter_elapsed,
                "received_from_node": _agent_flow()["extracter"][0],
                "sent_to_node": _agent_flow()["extracter"][1],
                "input_summary": f"{len(extracter_input)} message(s)",
                "questions_count": len(extracter_output.questions),
                "cache_hit": extracter_cache_hit,
            },
        )

    source_scope_retired = True
    if (
        container_manager.current is not None
        and container_manager.current.role == "extraction"
    ):
        source_scope_retired = await container_manager.retire_active(
            next_status="isolated"
        )
    delete_file = getattr(getattr(client, "files", None), "delete", None)
    for file_id in list(dict.fromkeys(file_ids)):
        retired = await _retire_owned_provider_resource(
            to_thread=to_thread,
            delete=delete_file,
            resource_id=file_id,
            owned_ids=file_ids,
            resource_kind="file",
        )
        source_scope_retired = source_scope_retired and retired
    if not source_scope_retired:
        raise RuntimeError(
            "Could not retire the untrusted extraction scope; "
            "downstream stages were not started"
        )

    if stop_after_agent == "extracter":
        workflow["status"] = "completed"
        touch_workflow(workflow)
        return _build_partial_result(
            normalizer_output=normalizer_output,
            extracter_output=extracter_output,
        )

    if needs_downstream_container:
        downstream_context = await container_manager.provision(
            role="downstream",
            file_ids=[],
        )
        agents = downstream_context.agents
        shared_runtime = downstream_context.runtime
        shared_container_id = downstream_context.container_id
    else:
        shared_runtime = None
        shared_container_id = ""

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
            max_turns=stage_max_turns("verifier"),
            stage_label="verifier",
            emit_token=emit_token,
            record_usage=record_usage,
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
                        max_turns=stage_max_turns("verifier"),
                        stage_label="verifier",
                        emit_token=emit_token,
                        record_usage=record_usage,
                    )
                repair_ids = set(verifier_report.repair_source_ids)
                existing_by_id: dict[str, list[SourceQuestionItem]] = {}
                repair_by_id: dict[str, list[SourceQuestionItem]] = {}
                for question in verifier_output.questions:
                    existing_by_id.setdefault(question.source_id, []).append(question)
                for question in repair_output.questions:
                    repair_by_id.setdefault(question.source_id, []).append(question)
                merged_questions: list[SourceQuestionItem] = []
                for source in extracter_output.questions:
                    selected = (
                        repair_by_id.pop(source.source_id, [])
                        if source.source_id in repair_ids
                        else existing_by_id.pop(source.source_id, [])
                    )
                    merged_questions.extend(selected)
                # Preserve unexpected/duplicate leftovers so deterministic
                # validation reports them instead of silently dropping them.
                for remaining in (*existing_by_id.values(), *repair_by_id.values()):
                    merged_questions.extend(remaining)
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

    expected_generated_count = _planned_generated_question_count(
        verifier_output.questions,
        normalizer_output.new_questions_per_question,
    )
    require_within_limit(
        "Planned generated question",
        expected_generated_count,
        generated_question_limit,
        "MAX_GENERATED_QUESTIONS",
    )

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
                extra=f"style-review={'on' if style_review_enabled else 'off'}",
            )
            if stage_cache.enabled
            else ""
        )
        cached_generator = await await_cancellation_safe_blocking_call(
            to_thread,
            stage_cache.lookup,
            generator_cache_key,
        )
        generator_cache_hit = cached_generator is not None
        if generator_cache_hit:
            generator_output: GeneratorOutput = GeneratorOutput.model_validate(cached_generator)
            logger.info("Workflow %s reused cached Generator output", workflow_id)
        else:
            generator_output = await run_llm_stage(
                agents["generator"],
                generator_input,
                run_config=run_config,
                max_turns=stage_max_turns("generator"),
                stage_label="generator",
                emit_token=emit_token,
                record_usage=record_usage,
            )

            # Guard against a flaky empty generation: retry once before the style
            # pass so a single degenerate response does not sink the whole run.
            if not generator_output.isomorphic_questions:
                retry_generator_output = await run_llm_stage(
                    agents["generator"],
                    generator_input,
                    run_config=run_config,
                    max_turns=stage_max_turns("generator"),
                    stage_label="generator",
                    emit_token=emit_token,
                    record_usage=record_usage,
                )
                if retry_generator_output.isomorphic_questions:
                    generator_output = retry_generator_output

            if style_review_enabled:
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
                        max_turns=stage_max_turns("generator"),
                        stage_label="generator",
                        emit_token=emit_token,
                        record_usage=record_usage,
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
                        max_turns=stage_max_turns("generator"),
                        stage_label="generator",
                        emit_token=emit_token,
                        record_usage=record_usage,
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
            await await_cancellation_safe_blocking_call(
                to_thread,
                stage_cache.store,
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
                "style_review_enabled": style_review_enabled,
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

    require_within_limit(
        "Generated question",
        len(generator_output.isomorphic_questions),
        generated_question_limit,
        "MAX_GENERATED_QUESTIONS",
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
        if shared_runtime is None:
            raise RuntimeError("Latexifier requires an active downstream container")
        workflow["agents"]["latexifier"]["status"] = "thinking"
        workflow["agents"]["latexifier"]["data"]["startTime"] = datetime.now().isoformat()
        touch_workflow(workflow)

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
        latex_output_ready = False
        try:
            latex_output: LaTeXOutput = await run_llm_stage(
                agents["latexifier"],
                latex_input,
                run_config=run_config,
                max_turns=stage_max_turns("latexifier"),
                stage_label="latexifier",
                emit_token=emit_token,
                record_usage=record_usage,
            )
            latex_output_ready = True
            (
                latex_output,
                latex_filename,
                latex_path,
                pdf_filename,
                pdf_path,
            ) = await materialize_latex_output_async(
                latex_output,
                outputs_dir,
                shared_runtime,
                to_thread=to_thread,
            )
        except Exception as exc:
            # The clean downstream container may expire during long stages.
            # Recreate it once without source-file IDs, rebuild the scoped
            # agents/tool closure, and let compile provision TeX lazily.
            if not _is_container_expired_error(exc):
                raise
            logger.warning(
                "Workflow %s latexifier hit a container error (%s); "
                "recreating the container and retrying once.",
                workflow_id,
                safe_exception_category(exc),
            )
            await container_manager.retire_active(next_status="expired")
            recreated_context = await container_manager.provision(
                role="downstream_recreate",
                file_ids=[],
            )
            shared_container_id = recreated_context.container_id
            shared_runtime = recreated_context.runtime
            agents = recreated_context.agents
            if not latex_output_ready:
                latex_output = await run_llm_stage(
                    agents["latexifier"],
                    latex_input,
                    run_config=run_config,
                    max_turns=stage_max_turns("latexifier"),
                    stage_label="latexifier",
                    emit_token=emit_token,
                    record_usage=record_usage,
                )
            (
                latex_output,
                latex_filename,
                latex_path,
                pdf_filename,
                pdf_path,
            ) = await materialize_latex_output_async(
                latex_output,
                outputs_dir,
                shared_runtime,
                to_thread=to_thread,
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
            latex_output, latex_filename, latex_path, pdf_filename, pdf_path = (
                await materialize_latex_output_async(
                    latex_output,
                    outputs_dir,
                    shared_runtime,
                    to_thread=to_thread,
                )
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

    # No later stage needs the Code Interpreter container. Retire it before
    # the potentially long-running paid Evals job so heartbeat and expiry
    # recovery cannot overlap unrelated provider work.
    if container_manager.current is not None:
        await container_manager.retire_active()
        shared_runtime = None
        shared_container_id = ""

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

        evaluation_report = await await_cancellation_safe_blocking_call(
            to_thread,
            run_openai_output_evaluation,
            client=client,
            final_result=evaluation_final_result,
            outputs_dir=outputs_dir,
            source_files=file_paths,
            contract_report=contract_report,
            should_cancel=lambda: bool(workflow.get("cancel_requested")),
        )
        evaluation_elapsed = time.time() - evaluation_stage_start

        # Update run_inputs names to downloadable relative paths and re-save.
        if input_name_map and isinstance(evaluation_report.get("run_inputs"), list):
            remaining_names = {
                source_name: list(relative_names)
                for source_name, relative_names in input_name_map.items()
            }
            for entry in evaluation_report["run_inputs"]:
                bare = Path(entry.get("name", "")).name
                matches = remaining_names.get(bare) or []
                if matches:
                    entry["name"] = matches.pop(0)
            eval_report_path_on_disk = (
                outputs_dir / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_REPORT_FILENAME
            )
            if eval_report_path_on_disk.exists():
                await to_thread(
                    eval_report_path_on_disk.write_text,
                    json.dumps(evaluation_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        workflow["agents"]["evaluation"]["data"]["endTime"] = datetime.now().isoformat()
        workflow["agents"]["evaluation"]["data"]["executionTime"] = evaluation_elapsed
        workflow["agents"]["evaluation"]["data"]["output"] = evaluation_report
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

    evaluation_status = (
        str(evaluation_report.get("status") or "").lower()
        if isinstance(evaluation_report, dict)
        else ""
    )
    if (
        should_run_evaluation
        and evaluation_status not in EVALUATION_SUCCESS_STATUSES
    ):
        reason = str(
            evaluation_report.get("reason")
            or evaluation_report.get("error")
            or (
                f"Evaluation ended with status {evaluation_status}"
                if evaluation_status
                else "Evaluation report did not include an explicit successful status"
            )
        )
        workflow["agents"]["evaluation"]["status"] = "errored"
        workflow["status"] = "errored"
        workflow["error"] = reason
        touch_workflow(workflow)
        raise ValueError(f"Evaluation did not complete successfully: {reason}")
    if should_run_evaluation:
        workflow["agents"]["evaluation"]["status"] = "completed"
        touch_workflow(workflow)

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
    for relative_name in input_relative_names:
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
        source_files=[
            str(outputs_dir / relative_name)
            for relative_name in input_relative_names
        ],
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

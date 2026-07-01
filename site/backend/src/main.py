"""CLI entrypoint for the isomorphic question generation workflow.

This module exposes an ``argparse`` subcommand surface that gives the CLI full
parity with the FastAPI web app. Every verb reuses the *shared* workflow engine
(``backend.src.workflow.run_workflow_async``) or the server's batch/eval-batch
job coroutines (``backend.src.api.state.run_create_data_batch_async`` /
``run_eval_batch_async``) rather than duplicating any orchestration. All
orchestration logic lives in ``backend.src.workflow.engine`` so the FastAPI
server and the CLI run the same code.

Subcommand map (CLI verb -> API endpoint):

    run                 POST /api/workflow/start
    rerun-from-agent    POST /api/workflow/rerun-from-agent
    from-agent-input    POST /api/workflow/start-from-agent-input
    batch               POST /api/create-data/start (+ status/result)
    eval                POST /api/eval-batch/start   (+ status/result)
    improve             (offline meta-evaluator tool; no web route)
    show                GET  /api/workflow/{id}/result (from final_result.json)
    zip                 GET  /api/download-zip/{id}
    list                GET  /api/agents /dropdown /prompts /rubric /evaluation/files
    prompt              GET  /api/prompts/{prompt_name}

Backward compatibility: the historical flat invocation
``python -m backend.src.main "<pdf>" -n 2 -i "..."`` still works. When the first
non-flag argument is not a known subcommand, the parser transparently dispatches
to ``run``.
"""

from __future__ import annotations

# Allow `python backend/src/main.py` (script mode) to use absolute backend.src
# imports without a try/except dance. In `python -m backend.src.main` mode the
# insert is a no-op because the repo root is already on sys.path.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Any

from agents.logger import logger
from openai import OpenAI

from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.evaluation import rubric_payload as _load_rubric_payload
from backend.src.paths import (
    ENV_PATH,
    EVALUATION_FIXTURES_DIR,
    OUTPUTS_DIR,
    PROMPTS_DIR,
    QUESTION_DATA_DIR,
)
from backend.src.registry import all_agents as _all_agents
from backend.src.registry import default_from_agent as _default_from_agent
from backend.src.registry import llm_agents as _llm_agents
from backend.src.registry import promptable_agents as _promptable_agents
from backend.src.registry import source_file_agents as _source_file_agents
from backend.src.registry import stoppable_agents as _stoppable_agents
from backend.src.registry import themes as _themes
from backend.src.safety import create_unique_run_dir
from backend.src.schemas import AGENT_INPUT_TYPES, WorkflowInput, WorkflowResult
from backend.src.workflow import run_workflow_async

_load_dotenv(ENV_PATH)

_cli_openai_client: OpenAI | None = None

# Verbs that consume subcommand parsing; anything else on the front of argv is
# treated as a bare `run` invocation for backward compatibility.
_SUBCOMMANDS = frozenset(
    {
        "run",
        "rerun-from-agent",
        "from-agent-input",
        "batch",
        "eval",
        "improve",
        "show",
        "zip",
        "list",
        "prompt",
    }
)


def get_cli_openai_client() -> OpenAI:
    """Lazily create the OpenAI client so `--help` does not need credentials."""
    global _cli_openai_client
    if _cli_openai_client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to run the workflow.")
        _cli_openai_client = OpenAI(api_key=api_key)
    return _cli_openai_client


async def _to_thread(func, /, *args, **kwargs):
    """Run blocking SDK/filesystem calls in the default executor (no semaphore for CLI)."""
    return await asyncio.to_thread(func, *args, **kwargs)


# ── Shared helpers ────────────────────────────────────────────────────────────


def _new_cli_workflow_record() -> dict[str, Any]:
    """Build the stub workflow dict the shared engine mutates in place.

    Reuses the server's ``_new_workflow_record`` factory so the CLI and API
    share one record shape; the agent keys derive from the registry sequence
    (no hardcoded stage names). The CLI discards the mutations after each run.
    """
    from backend.src.api.state import _new_workflow_record

    return _new_workflow_record()


def _resolve_output_folder(label: str) -> Path:
    """Resolve and validate an output-folder label under OUTPUTS_DIR.

    Mirrors the server's ``_resolve_output_folder_label`` traversal guard but
    raises plain errors (the CLI has no HTTP layer).
    """
    rel_path = Path((label or "").strip())
    if not rel_path.parts:
        raise ValueError("output folder label is required")
    if rel_path.is_absolute() or any(part in {"", ".", ".."} for part in rel_path.parts):
        raise ValueError(f"Invalid output folder label: {label!r}")
    output_dir = (OUTPUTS_DIR / rel_path).resolve()
    try:
        output_dir.relative_to(OUTPUTS_DIR.resolve())
    except ValueError as exc:
        raise ValueError(f"Invalid output folder label: {label!r}") from exc
    if not output_dir.exists() or not output_dir.is_dir():
        raise FileNotFoundError(f"Output folder not found: {label}")
    return output_dir


def _relative_output_folder(output_dir: Path) -> str:
    """Render an output directory relative to the outputs root."""
    try:
        return output_dir.resolve().relative_to(OUTPUTS_DIR.resolve()).as_posix()
    except Exception:
        return output_dir.name


def _resolve_test_files(rel_paths: list[str]) -> list[str]:
    """Resolve bundled test-file selections under EVALUATION_FIXTURES_DIR.

    Mirrors the server's ``testFiles`` handling in ``start_workflow``: each path
    is resolved under the fixtures dir and traversal outside is rejected.
    """
    from backend.src.api.state import ALLOWED_UPLOAD_EXTENSIONS, _is_under_directory

    resolved: list[str] = []
    for rel in rel_paths or []:
        candidate = (EVALUATION_FIXTURES_DIR / Path(str(rel))).resolve()
        if (
            _is_under_directory(candidate, EVALUATION_FIXTURES_DIR)
            and candidate.is_file()
            and candidate.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS
        ):
            resolved.append(str(candidate))
        else:
            raise FileNotFoundError(f"Test file not found under data/evaluation: {rel}")
    return resolved


# ── Core single-run engine wrapper ────────────────────────────────────────────


async def run_workflow(
    workflow_input: WorkflowInput,
    *,
    from_agent: str | None = None,
    agent_input: dict[str, Any] | None = None,
    stop_after: str | None = None,
    run_evaluation: bool = True,
    theme: str = "",
    resume_source_dir: Path | None = None,
    resume_source_label: str = "",
) -> WorkflowResult:
    """Run the pipeline by delegating to the shared engine.

    ``from_agent`` + ``agent_input`` start from a stage using a saved JSON
    payload; ``from_agent`` + ``resume_source_dir``/``resume_source_label``
    reuse a prior run's cached upstream outputs (rerun-from-agent). ``stop_after``
    stops the pipeline after the named agent completes. ``theme`` threads the UI
    theme string through the engine.
    """
    from_agent = from_agent or _default_from_agent()
    client = get_cli_openai_client()
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    run_label = f"cli-{timestamp}"
    outputs_dir, output_folder_label = create_unique_run_dir(OUTPUTS_DIR, run_label)
    workflow_record = _new_cli_workflow_record()
    workflow_id = run_label

    result = await run_workflow_async(
        client=client,
        workflow_id=workflow_id,
        workflow=workflow_record,
        prompt=workflow_input.user_instructions or "",
        file_paths=workflow_input.file_paths,
        theme=theme,
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        touch_workflow=lambda _record: None,
        to_thread=_to_thread,
        load_rubric_model=_load_rubric_model,
        load_rubric_payload=_load_rubric_payload,
        resume_from_agent=from_agent,
        resume_source_dir=resume_source_dir,
        resume_source_label=resume_source_label,
        agent_input_override=agent_input,
        agent_input_filename="",
        input_to_agent_run=agent_input is not None,
        run_evaluation=run_evaluation,
        stop_after_agent=stop_after,
    )

    logger.info("Output directory: %s", outputs_dir)
    return result


def _log_workflow_result(result: WorkflowResult) -> None:
    """Log the standard single-run completion summary."""
    logger.info("WORKFLOW COMPLETE")
    logger.info("Source questions:    %d", len(result.source_questions))
    logger.info("Verified questions:  %d", len(result.verified_questions))
    logger.info("Generated questions: %d", len(result.generated_questions))
    for q in result.generated_questions:
        logger.info("[Q%d] (from source %s): %s", q.id, q.source_id, q.stem[:80])


# ── Subcommand handlers ───────────────────────────────────────────────────────


def _cmd_run(args: argparse.Namespace) -> int:
    """Full workflow from the normalizer (POST /api/workflow/start)."""
    file_paths: list[str] = list(args.files or [])
    file_paths.extend(_resolve_test_files(args.test_file or []))

    from_agent = _default_from_agent()
    if from_agent in _source_file_agents() and not file_paths:
        default_file = QUESTION_DATA_DIR / "PDF" / "Quiz_Compilation.pdf"
        if default_file.exists():
            file_paths = [str(default_file)]
            logger.info("Using default file: %s", default_file)
        else:
            logger.error("No files provided and default file not found: %s", default_file)
            return 1

    instructions = args.instructions or ""
    if args.num_questions != 1:
        instructions = f"Generate {args.num_questions} questions per source. " + instructions

    theme = args.theme or ""
    if theme:
        valid = {str(t.get("label", "")).lower() for t in _themes()}
        if theme.lower() not in valid:
            logger.error(
                "Unknown theme %r. Choices: %s",
                theme,
                ", ".join(sorted(str(t.get("label", "")) for t in _themes())),
            )
            return 1

    workflow_input = WorkflowInput(file_paths=file_paths, user_instructions=instructions)
    result = asyncio.run(
        run_workflow(
            workflow_input,
            stop_after=args.stop_after,
            run_evaluation=not args.no_eval,
            theme=theme,
        )
    )
    _log_workflow_result(result)
    return 0


def _cmd_rerun_from_agent(args: argparse.Namespace) -> int:
    """Reuse a prior run's cached outputs before --from (rerun-from-agent)."""
    from backend.src.artifacts import load_source_file_paths

    start_agent = args.from_agent
    runnable = set(_llm_agents())
    if start_agent not in runnable:
        logger.error(
            "--from must be a runnable LLM stage: %s (got %r)",
            ", ".join(_llm_agents()),
            start_agent,
        )
        return 1

    source_dir = _resolve_output_folder(args.source)
    source_label = _relative_output_folder(source_dir)
    file_paths = load_source_file_paths(source_dir)
    if start_agent in _source_file_agents() and not file_paths:
        logger.error(
            "Source output folder %s has no reusable source file paths. "
            "Rerun from verifier or later, or run a fresh workflow.",
            source_label,
        )
        return 1

    workflow_input = WorkflowInput(file_paths=file_paths, user_instructions=args.instructions or "")
    result = asyncio.run(
        run_workflow(
            workflow_input,
            from_agent=start_agent,
            resume_source_dir=source_dir,
            resume_source_label=source_label,
        )
    )
    logger.info("Reused cached outputs from %s up to %s", source_label, start_agent)
    _log_workflow_result(result)
    return 0


def _cmd_from_agent_input(args: argparse.Namespace) -> int:
    """Start from a stage using a saved agent-input JSON (start-from-agent-input)."""
    from backend.src.workflow import extract_agent_input_payload

    start_agent = args.from_agent
    runnable = set(_llm_agents())
    if start_agent not in runnable:
        logger.error(
            "--from must be a runnable LLM stage: %s (got %r)",
            ", ".join(_llm_agents()),
            start_agent,
        )
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("--input file not found: %s", args.input)
        return 1
    try:
        agent_input_data = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load --input: %s", exc)
        return 1

    # Pre-validate against the agent's declared input schema, mirroring the
    # server's _validate_input_to_agent_payload intent so a malformed payload is
    # rejected before touching the engine (and before an API key is needed).
    structured = extract_agent_input_payload(agent_input_data)
    input_type = AGENT_INPUT_TYPES.get(start_agent)
    if not structured or input_type is None:
        logger.error("Schema match invalid for %s input", start_agent)
        return 1
    try:
        input_type.model_validate(structured)
    except Exception as exc:
        logger.error("Schema match invalid for %s input: %s", start_agent, exc)
        return 1

    workflow_input = WorkflowInput(file_paths=[], user_instructions=args.instructions or "")
    result = asyncio.run(
        run_workflow(
            workflow_input,
            from_agent=start_agent,
            agent_input=agent_input_data,
        )
    )
    logger.info("Started from %s using %s", start_agent, input_path.name)
    _log_workflow_result(result)
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    """Create-data batch over data/evaluation (POST /api/create-data/start)."""
    from backend.src.api.state import (
        _list_evaluation_source_files,
        _make_unique_output_folder_name,
        _sanitize_output_folder_name,
        _summarize_batch_entries,
        _workflow_now,
        batch_jobs,
        run_create_data_batch_async,
    )

    # Force an early credential failure with a clear message.
    get_cli_openai_client()

    evaluation_files = _list_evaluation_source_files()
    if not evaluation_files:
        logger.error("No evaluation files found in %s", EVALUATION_FIXTURES_DIR)
        return 1

    used_folder_names: set[str] = set()
    entries: list[dict[str, Any]] = []
    for path in evaluation_files:
        folder_name = _make_unique_output_folder_name(
            _sanitize_output_folder_name(path.stem),
            used_folder_names,
        )
        entries.append(
            {
                "name": path.name,
                "relativePath": path.relative_to(EVALUATION_FIXTURES_DIR).as_posix(),
                "folderName": folder_name,
                "status": "queued",
                "workflowId": "",
                "outputFolder": "",
                "error": "",
            }
        )

    batch_id = str(uuid.uuid4())
    now = _workflow_now()
    batch_jobs[batch_id] = {
        "status": "starting",
        "prompt": (args.instructions or "").strip(),
        "totalFiles": len(entries),
        "completedFiles": 0,
        "failedFiles": 0,
        "cancelledFiles": 0,
        "doneFiles": 0,
        "outputFolder": "",
        "entries": entries,
        "summaryFile": "",
        "error": "",
        "created_at": now,
        "updated_at": now,
    }

    logger.info("Create-data batch %s: %d file(s)", batch_id, len(entries))
    asyncio.run(run_create_data_batch_async(batch_id))

    batch = batch_jobs[batch_id]
    logger.info("BATCH COMPLETE (%s)", batch.get("status", "unknown"))
    logger.info("Output folder: %s", batch.get("outputFolder", ""))
    logger.info(
        "Completed %d / %d  (failed %d, cancelled %d)",
        batch.get("completedFiles", 0),
        batch.get("totalFiles", 0),
        batch.get("failedFiles", 0),
        batch.get("cancelledFiles", 0),
    )
    for entry in _summarize_batch_entries(batch.get("entries", [])):
        logger.info("  [%s] %s -> %s", entry["status"], entry["name"], entry["outputFolder"])
    return 0 if batch.get("failedFiles", 0) == 0 and batch.get("status") == "completed" else 1


def _cmd_eval(args: argparse.Namespace) -> int:
    """Eval-only batch over prior runs or PDF pairs (POST /api/eval-batch/start)."""
    from backend.src.api.state import (
        _summarize_eval_batch_entries,
        _workflow_now,
        eval_batch_jobs,
        run_eval_batch_async,
    )
    from backend.src.safety import safe_stem

    get_cli_openai_client()

    now = _workflow_now()
    batch_id = str(uuid.uuid4())

    if args.runs:
        entries = []
        for folder_name in args.runs:
            clean = safe_stem(str(folder_name), fallback="")
            if not clean:
                continue
            run_dir = OUTPUTS_DIR / clean
            if not run_dir.is_dir() or not (run_dir / "final_result.json").exists():
                logger.error("Run not found (needs final_result.json): %s", folder_name)
                return 1
            entries.append(
                {
                    "name": clean,
                    "folder": clean,
                    "status": "queued",
                    "score": None,
                    "itemsEvaluated": 0,
                    "outputFolder": "",
                    "error": "",
                }
            )
        if not entries:
            logger.error("No valid runs to evaluate")
            return 1
        batch = {
            "status": "starting",
            "mode": "prior_runs",
            "totalFiles": len(entries),
            "completedFiles": 0,
            "failedFiles": 0,
            "cancelledFiles": 0,
            "doneFiles": 0,
            "outputFolder": "",
            "entries": entries,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
    else:
        pairs = args.pairs or []
        if len(pairs) < 2:
            logger.error("--pairs needs a source PDF followed by one or more generated PDFs")
            return 1
        source_pdf = Path(pairs[0])
        if not source_pdf.exists():
            logger.error("Source PDF not found: %s", source_pdf)
            return 1
        entries = []
        for gen in pairs[1:]:
            gen_path = Path(gen)
            if not gen_path.exists():
                logger.error("Generated PDF not found: %s", gen_path)
                return 1
            entries.append(
                {
                    "name": gen_path.name,
                    "folder": gen_path.name,
                    "pdf_path": str(gen_path),
                    "status": "queued",
                    "score": None,
                    "itemsEvaluated": 0,
                    "outputFolder": "",
                    "error": "",
                }
            )
        batch = {
            "status": "starting",
            "mode": "upload",
            "source_pdf_path": str(source_pdf),
            "source_name": source_pdf.name,
            "totalFiles": len(entries),
            "completedFiles": 0,
            "failedFiles": 0,
            "cancelledFiles": 0,
            "doneFiles": 0,
            "outputFolder": "",
            "entries": entries,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }

    eval_batch_jobs[batch_id] = batch
    logger.info("Eval batch %s (%s): %d item(s)", batch_id, batch["mode"], len(entries))
    asyncio.run(run_eval_batch_async(batch_id))

    batch = eval_batch_jobs[batch_id]
    logger.info("EVAL BATCH COMPLETE (%s)", batch.get("status", "unknown"))
    logger.info("Output folder: %s", batch.get("outputFolder", ""))
    for entry in _summarize_eval_batch_entries(batch.get("entries", [])):
        logger.info(
            "  [%s] %s  score=%s  items=%s",
            entry["status"],
            entry["name"],
            entry.get("score"),
            entry.get("itemsEvaluated"),
        )
    return 0 if batch.get("failedFiles", 0) == 0 and batch.get("status") == "completed" else 1


def _cmd_improve(args: argparse.Namespace) -> int:
    """Run the propose-only meta-evaluator over an evaluation dir."""
    from backend.src.tools import meta_evaluator

    argv: list[str] = ["--eval-dir", str(args.eval_dir)]
    if args.labels:
        argv += ["--labels", str(args.labels)]
    if args.kappa_floor is not None:
        argv += ["--kappa-floor", str(args.kappa_floor)]
    return meta_evaluator.main(argv)


def _cmd_show(args: argparse.Namespace) -> int:
    """Print a run summary from final_result.json + evaluation_report.json."""
    output_dir = _resolve_output_folder(args.output_folder)
    label = _relative_output_folder(output_dir)
    result_path = output_dir / "final_result.json"
    if not result_path.exists():
        logger.error("No final_result.json in %s", label)
        return 1
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read final_result.json: %s", exc)
        return 1

    config = data.get("config") or {}
    print(f"Run: {label}")
    print("Config:")
    for key in ("title", "theme", "difficulty", "new_questions_per_question", "show_answers", "notes"):
        if key in config and config[key] not in (None, ""):
            print(f"  {key}: {config[key]}")
    print(f"Source questions:    {len(data.get('source_questions', []))}")
    print(f"Verified questions:  {len(data.get('verified_questions', []))}")
    print(f"Generated questions: {len(data.get('generated_questions', []))}")
    if data.get("latex_file"):
        print(f"LaTeX file: {data['latex_file']}")
    if data.get("pdf_file"):
        print(f"PDF file:   {data['pdf_file']}")

    report_path = output_dir / "evaluation_outputs" / "evaluation_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            print(f"Evaluation overall_score: {report.get('overall_score')}")
            print(f"Items evaluated: {report.get('items_evaluated')}")
        except Exception as exc:
            logger.warning("Could not read evaluation report: %s", exc)
    else:
        print("Evaluation: (no report)")
    return 0


def _cmd_zip(args: argparse.Namespace) -> int:
    """Build a zip archive of a run folder (GET /api/download-zip/{id})."""
    from backend.src.api.state import _build_workflow_zip

    output_dir = _resolve_output_folder(args.output_folder)
    label = _relative_output_folder(output_dir)
    zip_path = output_dir / f"{output_dir.name}.zip"
    _build_workflow_zip(output_dir, zip_path)
    print(f"Wrote {zip_path}")
    logger.info("Zipped %s -> %s", label, zip_path)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """Introspection over the registry / paths."""
    what = args.what
    if what == "agents":
        agents = _all_agents()
        for key, entry in agents.items():
            kind = entry.get("kind", "agent")
            print(f"{entry.get('sequence', '?'):>2}  {key:<18} [{kind}]  {entry.get('name', '')}")
    elif what == "themes":
        for theme in _themes():
            print(theme.get("label", ""))
    elif what == "prompts":
        for name in _promptable_agents():
            print(name)
    elif what == "rubric":
        print(json.dumps(_load_rubric_payload(), indent=2, ensure_ascii=False))
    elif what == "eval-files":
        from backend.src.api.state import _list_evaluation_source_files

        for path in _list_evaluation_source_files():
            print(path.relative_to(EVALUATION_FIXTURES_DIR).as_posix())
    elif what == "runs":
        if not OUTPUTS_DIR.exists():
            return 0
        found: list[tuple[float, str]] = []
        for result_path in OUTPUTS_DIR.rglob("final_result.json"):
            run_dir = result_path.parent
            label = _relative_output_folder(run_dir)
            try:
                mtime = run_dir.stat().st_mtime
            except OSError:
                mtime = 0.0
            found.append((mtime, label))
        for _mtime, label in sorted(found, reverse=True):
            print(label)
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    """Print an agent's prompt file (GET /api/prompts/{prompt_name})."""
    from backend.src.helpers import resolve_prompt_path
    from backend.src.registry import agent_entry

    name = args.agent
    allowed = set(_promptable_agents())
    prompt_name = name
    if name not in allowed:
        # Accept an agent key whose prompt file uses a promptName (e.g.
        # evaluation -> evaluator), matching the API's allowlist semantics.
        try:
            entry = agent_entry(name)
            prompt_name = entry.get("promptName") or name
        except KeyError:
            prompt_name = name
        if prompt_name not in allowed:
            logger.error("Prompt not found for %r. Choices: %s", name, ", ".join(sorted(allowed)))
            return 1

    prompt_path = resolve_prompt_path(PROMPTS_DIR, prompt_name)
    if prompt_path is None:
        logger.error("Prompt file not found: %s", prompt_name)
        return 1
    print(prompt_path.read_text(encoding="utf-8"))
    return 0


# ── Parser construction ───────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argparse subcommand surface."""
    from backend.src.registry import api_description as _api_description

    parser = argparse.ArgumentParser(prog="qgen", description=_api_description())
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    llm_choices = list(_llm_agents())
    stop_choices = list(_stoppable_agents())

    # run ---------------------------------------------------------------------
    p_run = subparsers.add_parser("run", help="Run a full workflow from the normalizer")
    p_run.add_argument("files", nargs="*", help="Path(s) to PDF/DOCX/image/text source files")
    p_run.add_argument("-i", "--instructions", default="", help="Extra generation instructions")
    p_run.add_argument(
        "-n",
        "--num-questions",
        type=int,
        default=1,
        help="Isomorphic questions per source question",
    )
    p_run.add_argument("--theme", default="", help="Theme label (see `list themes`)")
    p_run.add_argument(
        "--test-file",
        action="append",
        default=[],
        metavar="REL",
        help="Bundled data/evaluation file (relative path); repeatable",
    )
    p_run.add_argument(
        "--stop-after",
        dest="stop_after",
        choices=stop_choices,
        default=None,
        metavar="AGENT",
        help="Stop after this stage. Choices: " + ", ".join(stop_choices),
    )
    p_run.add_argument("--no-eval", dest="no_eval", action="store_true", help="Skip evaluation")
    p_run.set_defaults(func=_cmd_run)

    # rerun-from-agent --------------------------------------------------------
    p_rerun = subparsers.add_parser(
        "rerun-from-agent",
        help="Rerun a prior output folder starting at --from, reusing cached upstream outputs",
    )
    p_rerun.add_argument("--source", required=True, metavar="FOLDER", help="Prior output-folder label")
    p_rerun.add_argument(
        "--from",
        dest="from_agent",
        required=True,
        choices=llm_choices,
        metavar="AGENT",
        help="Start stage. Choices: " + ", ".join(llm_choices),
    )
    p_rerun.add_argument("-i", "--instructions", default="", help="Extra generation instructions")
    p_rerun.set_defaults(func=_cmd_rerun_from_agent)

    # from-agent-input --------------------------------------------------------
    p_fai = subparsers.add_parser(
        "from-agent-input",
        help="Start from --from using a saved agent-input JSON (schema-validated)",
    )
    p_fai.add_argument(
        "--from",
        dest="from_agent",
        required=True,
        choices=llm_choices,
        metavar="AGENT",
        help="Start stage. Choices: " + ", ".join(llm_choices),
    )
    p_fai.add_argument("--input", required=True, metavar="FILE", help="Saved agent-input JSON path")
    p_fai.add_argument("-i", "--instructions", default="", help="Extra generation instructions")
    p_fai.set_defaults(func=_cmd_from_agent_input)

    # batch -------------------------------------------------------------------
    p_batch = subparsers.add_parser("batch", help="Create-Data batch over data/evaluation")
    p_batch.add_argument("-i", "--instructions", default="", help="Prompt applied to every file")
    p_batch.set_defaults(func=_cmd_batch)

    # eval --------------------------------------------------------------------
    p_eval = subparsers.add_parser("eval", help="Eval-only batch over prior runs or PDF pairs")
    group = p_eval.add_mutually_exclusive_group(required=True)
    group.add_argument("--runs", nargs="+", metavar="FOLDER", help="Prior output-folder labels")
    group.add_argument(
        "--pairs",
        nargs="+",
        metavar="PDF",
        help="Source PDF followed by one or more generated PDFs",
    )
    p_eval.set_defaults(func=_cmd_eval)

    # improve -----------------------------------------------------------------
    p_improve = subparsers.add_parser("improve", help="Run the propose-only meta-evaluator")
    p_improve.add_argument("--eval-dir", dest="eval_dir", required=True, help="evaluation_outputs dir")
    p_improve.add_argument("--labels", default=None, help="Optional calibration JSONL for kappa gate")
    p_improve.add_argument(
        "--kappa-floor",
        dest="kappa_floor",
        type=float,
        default=None,
        help="Per-criterion Cohen's kappa floor",
    )
    p_improve.set_defaults(func=_cmd_improve)

    # show --------------------------------------------------------------------
    p_show = subparsers.add_parser("show", help="Print a run summary from its output folder")
    p_show.add_argument("output_folder", metavar="FOLDER", help="Output-folder label")
    p_show.set_defaults(func=_cmd_show)

    # zip ---------------------------------------------------------------------
    p_zip = subparsers.add_parser("zip", help="Zip a run's output folder")
    p_zip.add_argument("output_folder", metavar="FOLDER", help="Output-folder label")
    p_zip.set_defaults(func=_cmd_zip)

    # list --------------------------------------------------------------------
    p_list = subparsers.add_parser("list", help="Introspect agents/themes/prompts/rubric/eval-files/runs")
    p_list.add_argument(
        "what",
        choices=["agents", "themes", "prompts", "rubric", "eval-files", "runs"],
        help="What to list",
    )
    p_list.set_defaults(func=_cmd_list)

    # prompt ------------------------------------------------------------------
    p_prompt = subparsers.add_parser("prompt", help="Print an agent's prompt file")
    p_prompt.add_argument("agent", metavar="AGENT", help="Agent key or prompt name")
    p_prompt.set_defaults(func=_cmd_prompt)

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """Insert a default `run` subcommand for backward-compatible flat invocations.

    The historical CLI took leading positional file paths with no subcommand
    (``main.py <pdf> -n 2 -i "..."``). If the first non-flag token is not a known
    subcommand (and not a help flag), prepend ``run`` so that invocation still
    works verbatim.
    """
    if not argv:
        return argv
    for token in argv:
        if token in ("-h", "--help"):
            return argv
        if token.startswith("-"):
            # A leading flag before any positional (e.g. `-i foo file.pdf`) — the
            # old flat form. Route to run.
            return ["run", *argv]
        # First positional token: subcommand or a file path.
        if token in _SUBCOMMANDS:
            return argv
        return ["run", *argv]
    return argv


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    normalized = _normalize_argv(raw_argv)
    args = parser.parse_args(normalized)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        raise SystemExit(0)

    try:
        exit_code = func(args)
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("%s failed: %s", args.command or "run", exc)
        raise SystemExit(1) from exc

    raise SystemExit(int(exit_code or 0))


if __name__ == "__main__":
    main()

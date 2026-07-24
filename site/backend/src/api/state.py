"""Shared runtime state, config, and orchestration for the API layer.

This module owns the in-memory workflow/batch registries, the durable
``WorkflowStore``, configuration constants, and the helpers + workflow-runner
wrappers used by the API routers. It deliberately holds no FastAPI route
definitions so routers can import from one place without circular imports.
"""

import asyncio
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.logger import logger
from fastapi import HTTPException
from openai import OpenAI
from pydantic import ValidationError

from backend.src.artifacts import AGENT_DISPLAY_NAMES, WORKFLOW_AGENT_SEQUENCE
from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.evaluation import rubric_payload as _load_rubric_payload
from backend.src.paths import (
    ENV_PATH,
    LOCAL_ENV_PATH,
    OUTPUTS_DIR,
    RUNTIME_DIR,
    UPLOADS_DIR,
)
from backend.src.paths import (
    EVALUATION_FIXTURES_DIR as EVALUATION_DIR,
)
from backend.src.registry import (
    aliases_map as _aliases_map,
)
from backend.src.registry import (
    input_to_agent_sequence as _input_to_agent_sequence,
)
from backend.src.registry import (
    source_file_agents as _source_file_agents,
)
from backend.src.safety import (
    create_unique_run_dir,
    ensure_child_path,
    safe_filename,
    safe_stem,
    stable_short_hash,
    utc_now_iso,
    write_json,
)
from backend.src.schemas import AGENT_INPUT_TYPES
from backend.src.workflow import (
    extract_agent_input_payload as _extract_agent_input_payload,
)
from backend.src.workflow import (
    run_workflow_async as _engine_run_workflow_async,
)
from backend.src.workflow.resume import (
    agent_input_has_generated_questions as _agent_input_has_generated_questions,
)
from backend.src.workflow.store import WorkflowStore

# A developer's ignored local overrides load before the repository fallback.
# ``load_dotenv`` preserves values already supplied by the shell, so deployment
# environment variables always retain the highest precedence.
_load_dotenv(LOCAL_ENV_PATH)
_load_dotenv(ENV_PATH)

ALLOWED_UPLOAD_EXTENSIONS = {
    ".bmp",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".latex",
    ".markdown",
    ".md",
    ".pdf",
    ".png",
    ".svg",
    ".tex",
    ".tif",
    ".tiff",
    ".txt",
    ".webp",
}

ALLOWED_AGENT_INPUT_EXTENSIONS = {".json", ".jsonl", ".txt"}

SOURCE_FILE_INPUT_AGENTS: frozenset[str] = _source_file_agents()

# Every runnable stage (the canonical sequence minus the terminal "output" node).
INPUT_TO_AGENT_SEQUENCE: list[str] = _input_to_agent_sequence()

MAX_UPLOAD_FILES = 5

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# Total bytes accepted across all files in a single /api/upload request. Bounds a
# request even when it stays under the per-file cap on every file.
MAX_UPLOAD_TOTAL_BYTES = int(os.getenv("MAX_UPLOAD_TOTAL_BYTES", str(60 * 1024 * 1024)))

# Leading magic bytes required for content-sensitive upload types. A file whose
# declared extension is one of these must actually start with the signature
# (checked within the first KiB) — cheap defense against a disguised payload
# reaching the extracter's code-interpreter container.
UPLOAD_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
}

WORKFLOW_TTL_SECONDS = int(os.getenv("WORKFLOW_TTL_SECONDS", str(24 * 60 * 60)))

WORKFLOW_MAX_ENTRIES = int(os.getenv("WORKFLOW_MAX_ENTRIES", "200"))

OUTPUT_RETENTION_SECONDS = int(os.getenv("OUTPUT_RETENTION_SECONDS", str(7 * 24 * 60 * 60)))

OUTPUT_MAX_RUNS = int(os.getenv("OUTPUT_MAX_RUNS", "500"))

OUTPUT_MAX_BYTES = int(os.getenv("OUTPUT_MAX_BYTES", str(20 * 1024 * 1024 * 1024)))

UPLOAD_RETENTION_SECONDS = int(os.getenv("UPLOAD_RETENTION_SECONDS", str(24 * 60 * 60)))

UPLOAD_MAX_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))

ARTIFACT_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ARTIFACT_CLEANUP_INTERVAL_SECONDS", str(60 * 60)))

PRUNE_INTERVAL_SECONDS = max(5, int(os.getenv("PRUNE_INTERVAL_SECONDS", "60")))

ACTIVE_WORKFLOW_STALE_SECONDS = int(os.getenv("ACTIVE_WORKFLOW_STALE_SECONDS", str(6 * 60 * 60)))

OPENAI_WORKFLOW_BLOCKING_CONCURRENCY = max(1, int(os.getenv("OPENAI_WORKFLOW_BLOCKING_CONCURRENCY", "4")))

OPENAI_STATUS_URL = "https://status.openai.com/api/v2/status.json"

OPENAI_STATUS_TIMEOUT_SECONDS = float(os.getenv("OPENAI_STATUS_TIMEOUT_SECONDS", "5"))

BATCH_WORKFLOW_CONCURRENCY = max(1, int(os.getenv("BATCH_WORKFLOW_CONCURRENCY", "2")))

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

EVALUATION_DIR.mkdir(parents=True, exist_ok=True)

_openai_client: Optional[OpenAI] = None

workflow_store = WorkflowStore(RUNTIME_DIR / "workflows.sqlite3")

blocking_openai_semaphore = asyncio.Semaphore(OPENAI_WORKFLOW_BLOCKING_CONCURRENCY)

_last_artifact_cleanup_ts = 0.0

def _configured_allowed_origins() -> list[str]:
    """Return configured CORS origins."""
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

def _has_openai_api_key() -> bool:
    """Check whether an OpenAI API key is currently configured."""
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())

def get_openai_client() -> OpenAI:
    """
    Lazily initialize the OpenAI client.

    Keeping this lazy prevents the whole API from failing to start if
    OPENAI_API_KEY is missing; non-OpenAI endpoints can still function.
    """
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. "
            "Set it in the environment or in a .env file at the project root."
        )

    _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def replace_openai_api_key(api_key: str) -> None:
    """Apply a validated key replacement without ever returning the value."""
    global _openai_client
    value = (api_key or "").strip()
    if not value:
        raise ValueError("OpenAI API key cannot be empty")
    os.environ["OPENAI_API_KEY"] = value
    _openai_client = None

def _is_under_directory(path: Path, root: Path) -> bool:
    """Return True when `path` resolves inside `root`."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False

def _build_workflow_zip(output_dir: Path, zip_path: Path) -> None:
    """
    Zip every file in a workflow output folder into `zip_path`.

    Existing archives (and the in-progress temp file) are excluded so a rebuilt
    zip never nests an older one. The archive is written to a temp name and
    renamed into place so a concurrent download can't see a partial file.
    """
    tmp_path = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.suffix == ".zip" or path == tmp_path:
                continue
            archive.write(path, path.relative_to(output_dir).as_posix())
    tmp_path.replace(zip_path)

workflows: dict[str, dict] = {}

workflow_tasks: dict[str, asyncio.Task] = {}

# Per-workflow bounded queues of token deltas the engine emits (via emit_token)
# for the SSE "matrix" stream endpoint. Purely cosmetic: a full or absent queue
# just drops tokens; status polling remains the authoritative progress channel.
workflow_token_queues: dict[str, "asyncio.Queue"] = {}


def _make_emit_token(token_queue: "asyncio.Queue"):
    """Return an ``emit_token(agent_key, delta)`` that feeds the SSE stream queue."""

    def _emit(agent_key: str, delta: str) -> None:
        try:
            token_queue.put_nowait({"agent": agent_key, "delta": delta})
        except asyncio.QueueFull:
            pass

    return _emit

batch_jobs: dict[str, dict[str, Any]] = {}

batch_tasks: dict[str, asyncio.Task] = {}

eval_batch_jobs: dict[str, dict[str, Any]] = {}

eval_batch_tasks: dict[str, asyncio.Task] = {}

TERMINAL_STATUSES = {"completed", "errored", "cancelled", "lost", "blocked"}

LOST_STATUS = "lost"

UPLOAD_MANIFEST_FILENAME = "manifest.json"

def _workflow_now() -> str:
    """Return the server's workflow timestamp string."""
    return utc_now_iso()

def _workflow_id_for_record(target: dict[str, Any]) -> str:
    """Return a workflow's id, memoizing it on the record to avoid repeat scans."""
    cached = target.get("id")
    if cached:
        return str(cached)
    for workflow_id, workflow in workflows.items():
        if workflow is target:
            target["id"] = workflow_id
            return workflow_id
    return ""

def _batch_id_for_record(target: dict[str, Any]) -> str:
    """Return a batch's id, memoizing it on the record to avoid repeat scans."""
    cached = target.get("id")
    if cached:
        return str(cached)
    for batch_id, batch in batch_jobs.items():
        if batch is target:
            target["id"] = batch_id
            return batch_id
    return ""

def _persist_workflow(workflow_id: str, workflow: dict[str, Any]) -> None:
    """Persist a workflow record to SQLite."""
    if workflow_id:
        workflow_store.save("workflow", workflow_id, workflow)

def _persist_batch(batch_id: str, batch: dict[str, Any]) -> None:
    """Persist a batch record to SQLite."""
    if batch_id:
        workflow_store.save("batch", batch_id, batch)

def _get_workflow_record(workflow_id: str) -> dict[str, Any] | None:
    """Return a workflow from memory or durable store."""
    workflow = workflows.get(workflow_id)
    if workflow is not None:
        return workflow
    workflow = workflow_store.load("workflow", workflow_id)
    if workflow is not None:
        workflow["id"] = workflow_id
        workflows[workflow_id] = workflow
    return workflow

def _get_batch_record(batch_id: str) -> dict[str, Any] | None:
    """Return a batch from memory or durable store."""
    batch = batch_jobs.get(batch_id)
    if batch is not None:
        return batch
    batch = workflow_store.load("batch", batch_id)
    if batch is not None:
        batch["id"] = batch_id
        batch_jobs[batch_id] = batch
    return batch

def _touch_workflow(workflow: dict[str, Any]) -> None:
    """Update a workflow record's last-modified timestamp."""
    workflow["updated_at"] = _workflow_now()
    _persist_workflow(_workflow_id_for_record(workflow), workflow)

def _register_workflow_task(workflow_id: str, coroutine: Any) -> asyncio.Task:
    """Track process-local workflow tasks while durable status lives in SQLite."""
    task = asyncio.create_task(coroutine)
    workflow_tasks[workflow_id] = task

    def _on_task_done(done_task: asyncio.Task, current_id: str = workflow_id) -> None:
        workflow_tasks.pop(current_id, None)
        if done_task.cancelled():
            return
        # The failure is already recorded on the workflow record and logged by
        # _execute_workflow. Retrieve the exception here so asyncio doesn't emit
        # a spurious "Task exception was never retrieved" traceback for this
        # fire-and-forget background task.
        exc = done_task.exception()
        if exc is not None:
            logger.debug("Workflow %s task finished with error: %s", current_id, exc)

    task.add_done_callback(_on_task_done)
    return task

def _prune_workflows() -> None:
    """Bound memory by removing expired/old workflow records."""
    _cleanup_artifact_directories()
    now = datetime.now(timezone.utc).timestamp()
    expired_ids: list[str] = []
    for workflow_id, workflow in workflows.items():
        updated_raw = workflow.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated_ts = datetime.fromisoformat(updated_raw.replace("Z", "")).timestamp()
        except Exception:
            continue

        status = workflow.get("status")
        if status not in TERMINAL_STATUSES:
            task = workflow_tasks.get(workflow_id)
            task_is_live = bool(task and not task.done())
            if not task_is_live and now - updated_ts > ACTIVE_WORKFLOW_STALE_SECONDS:
                workflow["status"] = LOST_STATUS
                workflow["error"] = "Workflow heartbeat was lost before completion."
                workflow["errorStage"] = _active_agent_key(workflow)
                _persist_workflow(workflow_id, workflow)
            continue

        if now - updated_ts > WORKFLOW_TTL_SECONDS:
            expired_ids.append(workflow_id)

    for workflow_id in expired_ids:
        workflows.pop(workflow_id, None)
        workflow_tasks.pop(workflow_id, None)
        workflow_store.delete("workflow", workflow_id)

    if len(workflows) <= WORKFLOW_MAX_ENTRIES:
        return

    # Drop oldest terminal workflows first, then oldest overall.
    candidates = sorted(
        workflows.items(),
        key=lambda item: item[1].get("updated_at", ""),
    )
    for workflow_id, workflow in candidates:
        status = workflow.get("status")
        if status in TERMINAL_STATUSES:
            workflows.pop(workflow_id, None)
            workflow_tasks.pop(workflow_id, None)
            workflow_store.delete("workflow", workflow_id)
        if len(workflows) <= WORKFLOW_MAX_ENTRIES:
            break

def _prune_batch_jobs() -> None:
    """Bound memory by removing expired/old batch job records."""
    now = datetime.now(timezone.utc).timestamp()
    expired_ids: list[str] = []
    for batch_id, batch in batch_jobs.items():
        if batch.get("status") not in TERMINAL_STATUSES:
            continue
        updated_raw = batch.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated_ts = datetime.fromisoformat(updated_raw.replace("Z", "")).timestamp()
        except Exception:
            continue
        if now - updated_ts > WORKFLOW_TTL_SECONDS:
            expired_ids.append(batch_id)

    for batch_id in expired_ids:
        batch_jobs.pop(batch_id, None)
        workflow_store.delete("batch", batch_id)
        task = batch_tasks.pop(batch_id, None)
        if task and not task.done():
            task.cancel()

    if len(batch_jobs) <= WORKFLOW_MAX_ENTRIES:
        return

    candidates = sorted(
        batch_jobs.items(),
        key=lambda item: item[1].get("updated_at", ""),
    )
    for batch_id, batch in candidates:
        status = batch.get("status")
        if status in TERMINAL_STATUSES:
            batch_jobs.pop(batch_id, None)
            workflow_store.delete("batch", batch_id)
            task = batch_tasks.pop(batch_id, None)
            if task and not task.done():
                task.cancel()
        if len(batch_jobs) <= WORKFLOW_MAX_ENTRIES:
            break

def _is_old(record: dict, now: float) -> bool:
    raw = record.get("updated_at")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "")).timestamp()
        return now - ts > WORKFLOW_TTL_SECONDS
    except Exception:
        return False


def _prune_eval_batch_jobs() -> None:
    now = datetime.now(timezone.utc).timestamp()
    expired = [bid for bid, b in eval_batch_jobs.items() if b.get("status") in TERMINAL_STATUSES and _is_old(b, now)]
    for bid in expired:
        eval_batch_jobs.pop(bid, None)
        workflow_store.delete("batch", bid)
        task = eval_batch_tasks.pop(bid, None)
        if task and not task.done():
            task.cancel()


def _touch_eval_batch(batch: dict[str, Any]) -> None:
    batch["updated_at"] = _workflow_now()
    _persist_batch(_batch_id_for_record(batch), batch)


def _get_eval_batch_record(batch_id: str) -> dict[str, Any] | None:
    batch = eval_batch_jobs.get(batch_id)
    if batch is not None:
        return batch
    batch = workflow_store.load("batch", batch_id)
    if batch is not None:
        batch["id"] = batch_id
        eval_batch_jobs[batch_id] = batch
    return batch


def _refresh_eval_batch_progress(batch: dict[str, Any]) -> None:
    entries = batch.get("entries", [])
    batch["completedFiles"] = sum(1 for e in entries if e.get("status") == "completed")
    batch["failedFiles"] = sum(1 for e in entries if e.get("status") == "errored")
    batch["cancelledFiles"] = sum(1 for e in entries if e.get("status") == "cancelled")
    batch["doneFiles"] = batch["completedFiles"] + batch["failedFiles"] + batch["cancelledFiles"]


def _summarize_eval_batch_entries(entries: list[dict]) -> list[dict]:
    return [{"name": e.get("name",""), "folder": e.get("folder",""), "status": e.get("status","unknown"), "score": e.get("score"), "itemsEvaluated": e.get("itemsEvaluated",0), "outputFolder": e.get("outputFolder",""), "error": e.get("error","")} for e in entries]


def _run_summary(run_dir: Path) -> dict[str, Any] | None:
    """Summarize a run folder that contains a final_result.json.

    Returns None when the result file is missing or unreadable. Shared by
    ``_list_output_runs`` (eval-batch modal) and the CLI's ``list runs``.
    """
    result_path = run_dir / "final_result.json"
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    question_count = len(data.get("generated_questions", []))
    eval_report_path = run_dir / "evaluation_outputs" / "evaluation_report.json"
    existing_score = None
    has_eval = eval_report_path.exists()
    if has_eval:
        try:
            report = json.loads(eval_report_path.read_text(encoding="utf-8"))
            existing_score = report.get("overall_score")
        except Exception:
            pass
    return {
        "folder": run_dir.name,
        "label": run_dir.name,
        "mtime": run_dir.stat().st_mtime if run_dir.exists() else 0,
        "questionCount": question_count,
        "hasEval": has_eval,
        "existingScore": existing_score,
    }


def _list_output_runs(limit: int = 60) -> list[dict[str, Any]]:
    """List top-level output folders that contain a final_result.json, newest first.

    Deliberately non-recursive: eval-batch selections resolve as
    ``OUTPUTS_DIR / folder``, so only top-level runs are selectable here.
    """
    runs = []
    try:
        folders = sorted(OUTPUTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    except OSError:
        return []
    for folder in folders:
        if not folder.is_dir() or not (folder / "final_result.json").exists():
            continue
        summary = _run_summary(folder)
        if summary is None:
            continue
        runs.append(summary)
        if len(runs) >= limit:
            break
    return runs


async def run_eval_batch_async(batch_id: str) -> None:
    """Run a batch-eval job (prior runs or uploaded PDFs)."""
    import json as _json

    from agents import RunConfig, trace

    from backend.src.evaluation import (
        EVALUATION_OUTPUTS_DIRNAME,
        EVALUATION_REPORT_FILENAME,
        run_openai_output_evaluation,
    )

    batch = eval_batch_jobs[batch_id]
    mode = batch.get("mode", "prior_runs")
    client = get_openai_client()
    semaphore = asyncio.Semaphore(BATCH_WORKFLOW_CONCURRENCY)

    try:
        batch_output_dir, batch_output_label = create_unique_run_dir(OUTPUTS_DIR, batch_id, suffix="eval-batch")
        batch["status"] = "running"
        batch["outputFolder"] = batch_output_label
        _touch_eval_batch(batch)

        run_config = RunConfig(trace_metadata={"__trace_source__": "batch-eval", "batch_id": batch_id})

        if mode == "upload":
            from backend.src.eval_pipeline import extract_questions_from_pdf  # noqa: PLC0415
            source_pdf_path = Path(batch.get("source_pdf_path", ""))
            if not source_pdf_path.exists():
                raise FileNotFoundError(f"Source PDF not found: {source_pdf_path}")

            with trace("Batch Eval Upload"):
                source_extraction = await extract_questions_from_pdf(client=client, pdf_path=source_pdf_path, run_config=run_config, generated_pdf=False)
            write_json(batch_output_dir / "source_extraction.json", source_extraction.model_dump())

        async def _eval_one(entry: dict) -> None:
            async with semaphore:
                if batch.get("cancel_requested") or batch.get("status") == "cancelled":
                    entry["status"] = "cancelled"
                    entry["error"] = "Stopped by user"
                    _refresh_eval_batch_progress(batch)
                    _touch_eval_batch(batch)
                    return

                entry["status"] = "running"
                _refresh_eval_batch_progress(batch)
                _touch_eval_batch(batch)

                child_stem = safe_stem(entry.get("folder") or entry.get("name", "item"), fallback="item")
                child_dir = batch_output_dir / child_stem
                child_dir.mkdir(parents=True, exist_ok=True)

                try:
                    if mode == "prior_runs":
                        source_folder = OUTPUTS_DIR / entry["folder"]
                        result_path = source_folder / "final_result.json"
                        if not result_path.exists():
                            raise FileNotFoundError(f"final_result.json not found in {entry['folder']}")
                        final_result = _json.loads(result_path.read_text(encoding="utf-8"))
                    else:
                        from backend.src.eval_pipeline import (  # noqa: PLC0415
                            build_eval_final_result,
                            extract_questions_from_pdf,
                        )
                        gen_pdf = Path(entry["pdf_path"])
                        generated_extraction = await extract_questions_from_pdf(client=client, pdf_path=gen_pdf, run_config=run_config, generated_pdf=True)
                        write_json(child_dir / "generated_extraction.json", generated_extraction.model_dump())
                        final_result = build_eval_final_result(source_extraction=source_extraction, generated_extraction=generated_extraction, generated_pdf=gen_pdf)

                    write_json(child_dir / "final_result.json", final_result)

                    report = run_openai_output_evaluation(
                        client=client,
                        final_result=final_result,
                        outputs_dir=child_dir,
                        source_files=[],
                    )
                    # Recompute score excluding latex criterion (no tex file in batch eval)
                    criterion_scores = report.get("criterion_scores") or {}
                    numeric = [v.get("score") for v in criterion_scores.values() if isinstance(v, dict) and isinstance(v.get("score"), (int, float)) and v.get("criterion_name") != "latex_pdf_generated"]
                    overall = round(sum(float(s) for s in numeric) / len(numeric), 3) if numeric else None
                    report["overall_score"] = overall
                    report_path = child_dir / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_REPORT_FILENAME
                    write_json(report_path, report)

                    entry["status"] = "completed"
                    entry["score"] = overall
                    entry["itemsEvaluated"] = report.get("items_evaluated", 0)
                    entry["outputFolder"] = f"{batch_output_label}/{child_stem}"
                    entry["error"] = ""
                except Exception as exc:
                    write_json(child_dir / "error.json", {"status": "failed", "error": str(exc)})
                    entry["status"] = "errored"
                    entry["error"] = str(exc)
                finally:
                    _refresh_eval_batch_progress(batch)
                    _touch_eval_batch(batch)

        await asyncio.gather(*(_eval_one(e) for e in batch.get("entries", [])))

        _refresh_eval_batch_progress(batch)
        write_json(batch_output_dir / "batch_summary.json", {
            "generated_at": datetime.now().isoformat(),
            "batch_id": batch_id, "mode": mode,
            "output_folder": batch_output_label,
            "total_files": batch.get("totalFiles", 0),
            "completed_files": batch.get("completedFiles", 0),
            "failed_files": batch.get("failedFiles", 0),
            "entries": _summarize_eval_batch_entries(batch.get("entries", [])),
        })
        batch["error"] = f"{batch['failedFiles']} of {batch['totalFiles']} evals failed." if batch.get("failedFiles", 0) > 0 else ""
        batch["status"] = "completed"
        _touch_eval_batch(batch)
    except asyncio.CancelledError:
        batch["status"] = "cancelled"
        batch["error"] = "Stopped by user"
        batch["cancel_requested"] = True
        for entry in batch.get("entries", []):
            if entry.get("status") not in TERMINAL_STATUSES:
                entry["status"] = "cancelled"
                entry["error"] = "Stopped by user"
        _refresh_eval_batch_progress(batch)
        _touch_eval_batch(batch)
        raise
    except Exception as exc:
        logger.error("Eval batch %s failed: %s", batch_id, exc)
        batch["status"] = "errored"
        batch["error"] = str(exc)
        _touch_eval_batch(batch)


async def pruning_loop() -> None:
    """Periodically prune expired workflows/batches off the request path.

    Started from the FastAPI lifespan. Keeping pruning here (instead of calling it
    on every status poll) means the 150 ms client polling loop no longer triggers
    full registry scans and filesystem cleanup on each request.
    """
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_prune_workflows)
            await asyncio.to_thread(_prune_batch_jobs)
            await asyncio.to_thread(_prune_eval_batch_jobs)
        except Exception:
            logger.exception("Background pruning cycle failed")

def _directory_size(path: Path) -> int:
    """Return total size for files under a directory."""
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total

_DATED_DIR_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")


def _owning_run_label(label: str) -> str:
    """Return the leaf run-folder label that owns an output/upload `label`.

    For the dated layout the run folder is `<mm-dd-yyyy>/<hh-mm-ss>` (the first
    two segments); batch children add a third segment that we trim off. Legacy
    flat labels are returned as their first segment.
    """
    parts = [part for part in label.split("/") if part]
    if not parts:
        return label
    if _DATED_DIR_RE.match(parts[0]) and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _iter_artifact_run_dirs(base: Path) -> list[tuple[Path, str]]:
    """Return `(run_dir, relative_label)` for the leaf run dirs under `base`.

    Handles the dated layout (`<mm-dd-yyyy>/<run>`) and any legacy or internal
    flat dirs that live directly under `base` (e.g. `input_to_agent_*`).
    """
    runs: list[tuple[Path, str]] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if _DATED_DIR_RE.match(child.name):
            for run in child.iterdir():
                if run.is_dir():
                    runs.append((run, f"{child.name}/{run.name}"))
        else:
            runs.append((child, child.name))
    return runs


def _remove_empty_dated_dirs(base: Path) -> None:
    """Delete now-empty `<mm-dd-yyyy>` date folders left behind by pruning."""
    for child in base.iterdir():
        if not (child.is_dir() and _DATED_DIR_RE.match(child.name)):
            continue
        try:
            next(child.iterdir())
        except StopIteration:
            child.rmdir()
        except OSError:
            continue


def _active_output_labels() -> set[str]:
    """Return run-dir labels owned by active in-memory or persisted workflows."""
    labels: set[str] = set()
    records = list(workflows.items()) + workflow_store.list_records("workflow")
    for _workflow_id, workflow in records:
        if workflow.get("status") in TERMINAL_STATUSES:
            continue
        label = workflow.get("outputFolder")
        if isinstance(label, str) and label:
            labels.add(_owning_run_label(label))
    return labels

def _cleanup_artifact_directories() -> None:
    """Prune old upload/output folders without touching active workflows."""
    global _last_artifact_cleanup_ts
    now = time.time()
    if now - _last_artifact_cleanup_ts < ARTIFACT_CLEANUP_INTERVAL_SECONDS:
        return
    _last_artifact_cleanup_ts = now

    # Prune at the leaf run-dir level (the dated layout nests runs one level
    # below OUTPUTS_DIR/UPLOADS_DIR), then sweep away emptied date folders.
    active_outputs = _active_output_labels()
    output_runs = _iter_artifact_run_dirs(OUTPUTS_DIR)
    output_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
    for path, label in output_runs:
        if label in active_outputs:
            continue
        try:
            if now - path.stat().st_mtime > OUTPUT_RETENTION_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue

    output_runs = [
        item for item in _iter_artifact_run_dirs(OUTPUTS_DIR) if item[1] not in active_outputs
    ]
    output_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
    while len(output_runs) > OUTPUT_MAX_RUNS:
        shutil.rmtree(output_runs.pop(0)[0], ignore_errors=True)
    while (
        sum(_directory_size(item[0]) for item in output_runs if item[0].exists()) > OUTPUT_MAX_BYTES
        and output_runs
    ):
        shutil.rmtree(output_runs.pop(0)[0], ignore_errors=True)
    _remove_empty_dated_dirs(OUTPUTS_DIR)

    upload_runs = _iter_artifact_run_dirs(UPLOADS_DIR)
    upload_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
    for path, _label in upload_runs:
        try:
            if now - path.stat().st_mtime > UPLOAD_RETENTION_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
    upload_runs = _iter_artifact_run_dirs(UPLOADS_DIR)
    upload_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
    while (
        sum(_directory_size(item[0]) for item in upload_runs if item[0].exists()) > UPLOAD_MAX_BYTES
        and upload_runs
    ):
        shutil.rmtree(upload_runs.pop(0)[0], ignore_errors=True)
    _remove_empty_dated_dirs(UPLOADS_DIR)

def _touch_batch_job(batch: dict[str, Any]) -> None:
    """Update a batch job record's last-modified timestamp."""
    batch["updated_at"] = _workflow_now()
    _persist_batch(_batch_id_for_record(batch), batch)

def _new_workflow_record() -> dict[str, Any]:
    """Create the initial in-memory workflow record."""
    now = _workflow_now()
    return {
        "status": "starting",
        "container": {
            "status": "inactive",
            "id": "",
        },
        "agents": {
            key: {"status": "inactive", "data": {}} for key in WORKFLOW_AGENT_SEQUENCE
        },
        "files": [],
        "outputFolder": "",
        "result": None,
        "created_at": now,
        "updated_at": now,
    }

def _mark_workflow_cancelled(workflow: dict[str, Any], reason: str = "Stopped by user") -> None:
    """Mark a workflow and any running agents as cancelled."""
    workflow["status"] = "cancelled"
    workflow["error"] = reason
    now = datetime.now().isoformat()
    for agent in workflow.get("agents", {}).values():
        if agent.get("status") == "thinking":
            agent["status"] = "cancelled"
            data = agent.setdefault("data", {})
            data.setdefault("endTime", now)
            if data.get("startTime") and not data.get("executionTime"):
                try:
                    start = datetime.fromisoformat(str(data["startTime"]).replace("Z", ""))
                    data["executionTime"] = (datetime.now() - start).total_seconds()
                except Exception:
                    pass
        elif agent.get("status") in {"inactive", "queued"}:
            agent["status"] = "cancelled"
    _touch_workflow(workflow)

def _active_agent_key(workflow: dict[str, Any]) -> str | None:
    """Return the first currently running workflow stage."""
    for agent_key, agent in workflow.get("agents", {}).items():
        if agent.get("status") == "thinking":
            return agent_key
    return None

def _mark_workflow_error(workflow: dict[str, Any], error: str, stage: str | None = None) -> None:
    """Mark workflow and active agent as errored with actionable details."""
    error_stage = stage or _active_agent_key(workflow)
    workflow["status"] = "errored"
    workflow["error"] = error
    workflow["errorStage"] = error_stage
    now = datetime.now().isoformat()
    if error_stage and error_stage in workflow.get("agents", {}):
        agent = workflow["agents"][error_stage]
        agent["status"] = "errored"
        data = agent.setdefault("data", {})
        data["error"] = error
        data.setdefault("endTime", now)
        if data.get("startTime") and not data.get("executionTime"):
            try:
                start = datetime.fromisoformat(str(data["startTime"]).replace("Z", ""))
                data["executionTime"] = (datetime.now() - start).total_seconds()
            except Exception:
                pass
    _touch_workflow(workflow)
    # Billing/auth/server failures are operational incidents, not merely an
    # individual workflow error. Keep this best-effort so recording an alert
    # never hides the original workflow failure.
    try:
        from backend.src.operations import classify_openai_failure, operations, send_incident_email

        classified = classify_openai_failure(error)
        if classified:
            code, detail = classified
            incident = operations.open_incident(code=code, title=code.title(), detail=detail)
            operations.set_maintenance(active=True, code=code, message=detail)
            if incident.get("new"):
                send_incident_email(incident)
    except Exception:
        logger.exception("Could not record workflow operational incident")

def _workflow_status_payload(workflow_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    """Serialize workflow status for polling and SSE."""
    return {
        "workflowId": workflow_id,
        "status": workflow.get("status", "unknown"),
        "agents": workflow.get("agents", {}),
        "outputFolder": workflow.get("outputFolder", ""),
        "error": workflow.get("error", ""),
        "errorStage": workflow.get("errorStage"),
        "blocked": workflow.get("blocked"),
    }

def _downloadable_file_names(workflow: dict[str, Any]) -> set[str]:
    """Return the allowlisted downloadable file names for a workflow."""
    names: set[str] = set()
    for item in workflow.get("files", []):
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and item.get("downloadable", True):
                names.add(name)
        elif isinstance(item, str):
            names.add(item)
    return names

def _relative_output_folder(output_dir: Path) -> str:
    """Render an output directory relative to the backend outputs root."""
    try:
        return output_dir.resolve().relative_to(OUTPUTS_DIR.resolve()).as_posix()
    except Exception:
        return output_dir.name

def _sanitize_output_folder_name(name: str) -> str:
    """Sanitize a user-supplied folder name for filesystem use."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "submission"

def _make_unique_output_folder_name(base_name: str, used_names: set[str]) -> str:
    """Return a unique output folder name within the current submission set."""
    candidate = base_name
    suffix = 2
    while candidate in used_names:
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate

def _normalize_agent_key(value: str) -> str:
    """Normalize user input into a canonical agent key."""
    raw = (value or "").strip().lower()
    if raw.isdigit():
        index = int(raw) - 1
        runnable_sequence = WORKFLOW_AGENT_SEQUENCE[:-1]
        if 0 <= index < len(runnable_sequence):
            return runnable_sequence[index]
    normalized = raw.replace(" ", "_").replace("-", "_")
    aliases = _aliases_map()
    return aliases.get(normalized, normalized)

class OutputFolderError(ValueError):
    """Raised when an output-folder label is missing, malformed, or absent on disk."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found

def _resolve_output_folder_path(label: str) -> Path:
    """Resolve and validate an output-folder label under OUTPUTS_DIR.

    Transport-agnostic core shared by the HTTP layer
    (``_resolve_output_folder_label``) and the CLI
    (``main._resolve_output_folder``).

    Raises:
        OutputFolderError: If the label is missing, malformed, points outside
            the outputs directory, or (with ``not_found=True``) does not exist.
    """
    rel_path = Path((label or "").strip())
    if not rel_path.parts:
        raise OutputFolderError("output folder label is required")
    if rel_path.is_absolute() or any(part in {"", ".", ".."} for part in rel_path.parts):
        raise OutputFolderError(f"Invalid output folder label: {label!r}")
    output_dir = (OUTPUTS_DIR / rel_path).resolve()
    if not _is_under_directory(output_dir, OUTPUTS_DIR):
        raise OutputFolderError(f"Invalid output folder label: {label!r}")
    if not output_dir.exists() or not output_dir.is_dir():
        raise OutputFolderError(f"Output folder not found: {label}", not_found=True)
    return output_dir

def _resolve_output_folder_label(label: str) -> Path:
    """Resolve and validate a requested output-folder label.

    Raises:
        HTTPException: If the label is missing, malformed, or points outside the
            outputs directory.
    """
    try:
        return _resolve_output_folder_path(label)
    except OutputFolderError as exc:
        if exc.not_found:
            raise HTTPException(status_code=404, detail="Source output folder not found") from exc
        if not (label or "").strip():
            raise HTTPException(status_code=400, detail="sourceOutputFolder is required") from exc
        raise HTTPException(status_code=400, detail="Invalid sourceOutputFolder") from exc

def _resolve_evaluation_fixture_paths(rel_paths: list, *, strict: bool) -> list[str]:
    """Resolve bundled data/evaluation selections to absolute file paths.

    Shared by the workflow-start route (``strict=False``: silently skip bad
    entries) and the CLI's ``--test-file`` handling (``strict=True``: raise on
    the first bad entry).
    """
    resolved: list[str] = []
    for rel_path in rel_paths or []:
        try:
            rel = Path(str(rel_path))
        except Exception:
            if strict:
                raise FileNotFoundError(f"Test file not found under data/evaluation: {rel_path}") from None
            continue
        candidate = (EVALUATION_DIR / rel).resolve()
        if (
            _is_under_directory(candidate, EVALUATION_DIR)
            and candidate.is_file()
            and candidate.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS
        ):
            resolved.append(str(candidate))
        elif strict:
            raise FileNotFoundError(f"Test file not found under data/evaluation: {rel_path}")
    return resolved

def _parse_agent_input_upload(content: bytes, filename: str) -> Any:
    """Parse an uploaded agent input file into a Runner-compatible payload."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Agent input file exceeds {MAX_UPLOAD_BYTES} bytes",
        )

    try:
        text = content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Agent input file must be UTF-8 text") from exc

    if not text:
        raise HTTPException(status_code=400, detail="Agent input file is empty")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records = []
                break
        if records:
            return records[-1]

    if Path(filename).suffix.lower() in {".json", ".jsonl"}:
        raise HTTPException(
            status_code=400,
            detail="Schema match invalid",
        )
    return text

def _save_input_to_agent_source_file(
    *,
    workflow_id: str,
    filename: str,
    content: bytes,
) -> Path:
    """Persist a source document uploaded through the Input To Agent modal."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Source file exceeds {MAX_UPLOAD_BYTES} bytes",
        )

    stored_filename = safe_filename(
        filename or "source.txt",
        fallback="source.txt",
        allowed_suffixes=ALLOWED_UPLOAD_EXTENSIONS,
    )
    suffix = Path(stored_filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Normalizer and Extracter starts need a source file with one of: "
                + ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            ),
        )

    session_dir = UPLOADS_DIR / f"input_to_agent_{workflow_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / stored_filename
    file_path.write_bytes(content)
    return file_path

def _upload_manifest_path(session_dir: Path) -> Path:
    """Return the upload session manifest path."""
    return session_dir / UPLOAD_MANIFEST_FILENAME

def _load_upload_manifest(session_dir: Path) -> dict[str, Any]:
    """Load an upload manifest, if present."""
    manifest_path = _upload_manifest_path(session_dir)
    if not manifest_path.exists():
        return {"files": []}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": []}
    return data if isinstance(data, dict) else {"files": []}

def _write_upload_manifest(session_dir: Path, manifest: dict[str, Any]) -> None:
    """Persist an upload session manifest."""
    write_json(_upload_manifest_path(session_dir), manifest)

def _source_paths_from_session(session_id: str, selected_file_ids: list[str] | None = None) -> list[str]:
    """Resolve upload session file IDs to safe local source paths."""
    # sessionId is a posix-relative label (e.g. 06-29-2026/14-23-05 for the
    # dated layout, or a legacy flat token). Validate every part to block
    # traversal, then resolve under UPLOADS_DIR.
    rel_path = Path((session_id or "").strip())
    if (
        not rel_path.parts
        or rel_path.is_absolute()
        or any(part in {"", ".", ".."} or part.startswith(".") for part in rel_path.parts)
    ):
        raise HTTPException(status_code=400, detail="Invalid sessionId")
    session_dir = (UPLOADS_DIR / rel_path).resolve()
    if not _is_under_directory(session_dir, UPLOADS_DIR) or not session_dir.exists():
        return []

    manifest = _load_upload_manifest(session_dir)
    selected = set(selected_file_ids or [])
    paths: list[str] = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("fileId") or "")
        if selected and file_id not in selected:
            continue
        stored_name = str(item.get("storedName") or "")
        try:
            path = ensure_child_path(session_dir, stored_name)
        except Exception:
            continue
        if path.is_file() and path.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS:
            paths.append(str(path))
    return paths

async def _to_thread_limited(func, /, *args, **kwargs):
    """Run blocking SDK/filesystem work in a bounded thread pool."""
    async with blocking_openai_semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)

def _raise_schema_match_invalid(start_agent: str, exc: Exception | None = None) -> None:
    """Raise the standard agent-input schema validation error."""
    if exc is not None:
        logger.warning("Schema match invalid for %s input: %s", start_agent, exc)
    raise HTTPException(status_code=400, detail="Schema match invalid")

def _validate_input_to_agent_payload(start_agent: str, payload: Any) -> None:
    """Validate uploaded agent input against the selected agent's input schema."""
    structured_payload = _extract_agent_input_payload(payload)
    if not structured_payload:
        _raise_schema_match_invalid(start_agent)

    input_type = AGENT_INPUT_TYPES.get(start_agent)
    if input_type is None:
        _raise_schema_match_invalid(start_agent)

    try:
        input_type.model_validate(structured_payload)
    except ValidationError as exc:
        _raise_schema_match_invalid(start_agent, exc)

    if start_agent == "evaluation" and not _agent_input_has_generated_questions(structured_payload):
        _raise_schema_match_invalid(start_agent)

def _list_evaluation_source_files() -> list[Path]:
    """Return uploadable files from the evaluation data directory."""
    if not EVALUATION_DIR.exists():
        return []

    files: list[Path] = []
    for path in sorted(EVALUATION_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            continue
        files.append(path)
    return files

def _parse_form_bool(value: str, *, default: bool = False) -> bool:
    """Parse a boolean value submitted by browser FormData."""
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default

# (key, display name) for every stage, derived from the canonical artifacts maps.
CREATE_DATA_STEP_SEQUENCE: list[tuple[str, str]] = [
    (key, AGENT_DISPLAY_NAMES[key]) for key in WORKFLOW_AGENT_SEQUENCE
]

def _get_batch_entry_step(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized step state for a batch entry."""
    total_steps = len(CREATE_DATA_STEP_SEQUENCE)
    step_index = 1
    step_key, step_name = CREATE_DATA_STEP_SEQUENCE[0]

    workflow_id = entry.get("workflowId", "")
    workflow = _get_workflow_record(workflow_id) if workflow_id else None
    if workflow:
        agents = workflow.get("agents", {})
        for index, (candidate_key, candidate_name) in enumerate(
            CREATE_DATA_STEP_SEQUENCE,
            start=1,
        ):
            candidate_status = agents.get(candidate_key, {}).get("status", "inactive")
            if candidate_status != "completed":
                step_index = index
                step_key = candidate_key
                step_name = candidate_name
                break
        else:
            step_index = total_steps
            step_key, step_name = CREATE_DATA_STEP_SEQUENCE[-1]

    if entry.get("status") == "completed":
        step_index = total_steps
        step_key, step_name = CREATE_DATA_STEP_SEQUENCE[-1]

    return {
        "currentStepIndex": step_index,
        "totalSteps": total_steps,
        "currentAgentKey": step_key,
        "currentAgentName": step_name,
        "stepLabel": f"{step_index}/{total_steps}",
    }

def _summarize_batch_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize batch entries for the API response payload."""
    summary: list[dict[str, Any]] = []
    for entry in entries:
        step = _get_batch_entry_step(entry)
        summary.append(
            {
                "name": entry.get("name", ""),
                "relativePath": entry.get("relativePath", ""),
                "folderName": entry.get("folderName", ""),
                "status": entry.get("status", "unknown"),
                "workflowId": entry.get("workflowId", ""),
                "outputFolder": entry.get("outputFolder", ""),
                "error": entry.get("error", ""),
                "currentStepIndex": step["currentStepIndex"],
                "totalSteps": step["totalSteps"],
                "currentAgentKey": step["currentAgentKey"],
                "currentAgentName": step["currentAgentName"],
                "stepLabel": step["stepLabel"],
            }
        )
    return summary

def _refresh_batch_progress(batch: dict[str, Any]) -> None:
    """Recompute aggregate batch counters from the entry list."""
    entries = batch.get("entries", [])
    completed = sum(1 for entry in entries if entry.get("status") == "completed")
    failed = sum(1 for entry in entries if entry.get("status") == "errored")
    cancelled = sum(1 for entry in entries if entry.get("status") == "cancelled")
    batch["completedFiles"] = completed
    batch["failedFiles"] = failed
    batch["cancelledFiles"] = cancelled
    batch["doneFiles"] = completed + failed + cancelled

async def run_workflow_async(
    workflow_id: str,
    prompt: str,
    file_paths: list[str],
    *,
    run_evaluation: bool = True,
    theme: str = "",
):
    """Run the workflow asynchronously."""
    workflow = workflows[workflow_id]
    outputs_dir, output_folder_label = create_unique_run_dir(OUTPUTS_DIR, workflow_id)
    await _execute_workflow(
        workflow_id,
        workflow,
        prompt,
        file_paths,
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        run_evaluation=run_evaluation,
        theme=theme,
    )

async def run_workflow_from_agent_async(
    workflow_id: str,
    prompt: str,
    file_paths: list[str],
    *,
    start_agent: str,
    resume_source_dir: Path,
    resume_source_label: str,
):
    """Run a workflow from start_agent using cached upstream agent outputs."""
    workflow = workflows[workflow_id]
    outputs_dir, output_folder_label = create_unique_run_dir(
        OUTPUTS_DIR,
        workflow_id,
        suffix=f"from-{start_agent}",
    )
    await _execute_workflow(
        workflow_id,
        workflow,
        prompt,
        file_paths,
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        resume_from_agent=start_agent,
        resume_source_dir=resume_source_dir,
        resume_source_label=resume_source_label,
    )

async def run_workflow_from_agent_input_async(
    workflow_id: str,
    prompt: str,
    *,
    file_paths: list[str],
    start_agent: str,
    agent_input_payload: Any = None,
    input_filename: str,
):
    """Run a workflow from start_agent using an uploaded source or agent input payload."""
    workflow = workflows[workflow_id]
    outputs_dir, output_folder_label = create_unique_run_dir(
        OUTPUTS_DIR,
        workflow_id,
        suffix=f"input-to-{start_agent}",
    )
    await _execute_workflow(
        workflow_id,
        workflow,
        prompt,
        file_paths,
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        resume_from_agent=start_agent,
        agent_input_override=agent_input_payload,
        agent_input_filename=input_filename,
        input_to_agent_run=True,
    )

async def _execute_workflow(
    workflow_id: str,
    workflow: dict[str, Any],
    prompt: str,
    file_paths: list[str],
    *,
    outputs_dir: Path,
    output_folder_label: str,
    resume_from_agent: str = "normalizer",
    resume_source_dir: Optional[Path] = None,
    resume_source_label: str = "",
    agent_input_override: Any = None,
    agent_input_filename: str = "",
    input_to_agent_run: bool = False,
    run_evaluation: bool = True,
    theme: str = "",
) -> None:
    """Run the workflow via the shared engine; surface FastAPI cancel/error states."""
    client = get_openai_client()
    token_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    workflow_token_queues[workflow_id] = token_queue
    try:
        await _engine_run_workflow_async(
            client=client,
            workflow_id=workflow_id,
            workflow=workflow,
            prompt=prompt,
            file_paths=file_paths,
            theme=theme,
            outputs_dir=outputs_dir,
            output_folder_label=output_folder_label,
            touch_workflow=_touch_workflow,
            to_thread=_to_thread_limited,
            load_rubric_model=_load_rubric_model,
            load_rubric_payload=_load_rubric_payload,
            resume_from_agent=resume_from_agent,
            resume_source_dir=resume_source_dir,
            resume_source_label=resume_source_label,
            agent_input_override=agent_input_override,
            agent_input_filename=agent_input_filename,
            input_to_agent_run=input_to_agent_run,
            run_evaluation=run_evaluation,
            emit_token=_make_emit_token(token_queue),
        )
    except asyncio.CancelledError:
        logger.info("Workflow %s cancelled", workflow_id)
        _mark_workflow_cancelled(workflow)
        raise
    except Exception as e:
        logger.error("Workflow %s failed: %s", workflow_id, e)
        _mark_workflow_error(workflow, str(e))
        raise
    finally:
        # Drop the registry reference; an attached SSE stream keeps its own local
        # reference to drain the tail, then ends on the terminal status.
        workflow_token_queues.pop(workflow_id, None)

async def run_create_data_batch_async(batch_id: str):
    """Run the workflow against every evaluation file in parallel."""
    batch = batch_jobs[batch_id]
    semaphore = asyncio.Semaphore(BATCH_WORKFLOW_CONCURRENCY)

    try:
        batch_output_dir, batch_output_label = create_unique_run_dir(
            OUTPUTS_DIR,
            batch_id,
            suffix="batch",
        )
        batch["status"] = "running"
        batch["outputFolder"] = batch_output_label
        _touch_batch_job(batch)

        async def run_entry(entry: dict[str, Any]) -> None:
            """Run one evaluation-file workflow and record its batch status."""
            async with semaphore:
                if batch.get("cancel_requested") or batch.get("status") == "cancelled":
                    entry["status"] = "cancelled"
                    entry["error"] = "Stopped by user"
                    _refresh_batch_progress(batch)
                    _touch_batch_job(batch)
                    return

                entry["status"] = "running"
                _refresh_batch_progress(batch)
                _touch_batch_job(batch)

                workflow_id = str(uuid.uuid4())
                entry["workflowId"] = workflow_id
                workflows[workflow_id] = _new_workflow_record()
                _persist_workflow(workflow_id, workflows[workflow_id])

                workflow = workflows[workflow_id]
                relative_path = entry.get("relativePath", "")
                source_path = (EVALUATION_DIR / relative_path).resolve()
                child_name = f"{safe_stem(str(entry.get('folderName') or source_path.stem), fallback='item')}-{stable_short_hash(relative_path)}"
                output_folder_label = f"{batch_output_label}/{child_name}"
                child_output_dir = batch_output_dir / child_name

                try:
                    if not _is_under_directory(source_path, EVALUATION_DIR) or not source_path.is_file():
                        raise FileNotFoundError(f"Evaluation file not found: {relative_path}")

                    await _execute_workflow(
                        workflow_id,
                        workflow,
                        batch.get("prompt", ""),
                        [str(source_path)],
                        outputs_dir=child_output_dir,
                        output_folder_label=output_folder_label,
                    )
                    entry["status"] = "completed"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = ""
                except asyncio.CancelledError:
                    _mark_workflow_cancelled(workflow)
                    entry["status"] = "cancelled"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = "Stopped by user"
                    raise
                except Exception as exc:
                    workflow["outputFolder"] = output_folder_label
                    _mark_workflow_error(workflow, str(exc))
                    entry["status"] = "errored"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = str(exc)
                finally:
                    _refresh_batch_progress(batch)
                    _touch_batch_job(batch)

        await asyncio.gather(*(run_entry(entry) for entry in batch.get("entries", [])))

        _refresh_batch_progress(batch)
        summary_path = batch_output_dir / "batch_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(),
                    "batch_id": batch_id,
                    "prompt": batch.get("prompt", ""),
                    "output_folder": batch.get("outputFolder", ""),
                    "total_files": batch.get("totalFiles", 0),
                    "completed_files": batch.get("completedFiles", 0),
                    "failed_files": batch.get("failedFiles", 0),
                    "cancelled_files": batch.get("cancelledFiles", 0),
                    "entries": _summarize_batch_entries(batch.get("entries", [])),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        batch["summaryFile"] = summary_path.name
        batch["error"] = (
            f"{batch['failedFiles']} of {batch['totalFiles']} file workflows failed."
            if batch.get("failedFiles", 0) > 0
            else ""
        )
        batch["status"] = "completed"
        _touch_batch_job(batch)
        logger.info("Create-data batch %s completed", batch_id)
    except asyncio.CancelledError:
        logger.info("Create-data batch %s cancelled", batch_id)
        batch["status"] = "cancelled"
        batch["error"] = "Stopped by user"
        batch["cancel_requested"] = True
        for entry in batch.get("entries", []):
            if entry.get("status") in TERMINAL_STATUSES:
                continue
            entry["status"] = "cancelled"
            entry["error"] = "Stopped by user"
            workflow_id = entry.get("workflowId", "")
            workflow = _get_workflow_record(workflow_id) if workflow_id else None
            if workflow:
                _mark_workflow_cancelled(workflow)
        _refresh_batch_progress(batch)
        _touch_batch_job(batch)
        raise
    except Exception as exc:
        logger.error("Create-data batch %s failed: %s", batch_id, exc)
        batch["status"] = "errored"
        batch["error"] = str(exc)
        _touch_batch_job(batch)

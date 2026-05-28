"""
FastAPI Server for Isomorphic Question Generator

Provides REST API endpoints for:
- File upload
- Workflow execution with SSE streaming
- File downloads
- Static file serving for the frontend
"""

# Allow `python backend/src/server.py` (script mode) to use absolute backend.src
# imports without a try/except dance. In `python -m backend.src.server` and
# `uvicorn backend.src.server:app` modes the insert is a harmless no-op.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agents.logger import logger
from openai import OpenAI

from backend.src.artifacts import WORKFLOW_AGENT_SEQUENCE, load_source_file_paths
from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.evaluation import rubric_payload as _load_rubric_payload
from backend.src.helpers import resolve_prompt_path
from backend.src.paths import (
    AGENTS_CONFIG_PATH,
    BACKEND_ROOT,
    DATA_DIR,
    DIST_DIR,
    ENV_PATH,
    EVALUATION_FIXTURES_DIR as EVALUATION_DIR,
    OUTPUTS_DIR,
    PROMPTS_DIR,
    REPO_ROOT,
    RUNTIME_DIR,
    UPLOADS_DIR,
    VERSION_PATHS,
)
from backend.src.safety import (
    create_unique_run_dir,
    ensure_child_path,
    safe_filename,
    safe_stem,
    stable_short_hash,
    unique_child_name,
    write_json,
)
from backend.src.schemas import AGENT_INPUT_TYPES
from backend.src.workflow import (
    extract_agent_input_payload as _extract_agent_input_payload,
    run_workflow_async,
)
from backend.src.workflow.resume import agent_input_has_generated_questions as _agent_input_has_generated_questions
from backend.src.workflow.store import WorkflowStore

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = BACKEND_ROOT
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
ALLOWED_AGENT_INPUT_EXTENSIONS = {".json", ".jsonl", ".txt"}
SOURCE_FILE_INPUT_AGENTS = {"normalizer", "extracter"}
INPUT_TO_AGENT_SEQUENCE = [
    "normalizer",
    "extracter",
    "verifier",
    "generator",
    "latexifier",
    "evaluation",
]
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
WORKFLOW_TTL_SECONDS = int(os.getenv("WORKFLOW_TTL_SECONDS", str(24 * 60 * 60)))
WORKFLOW_MAX_ENTRIES = int(os.getenv("WORKFLOW_MAX_ENTRIES", "200"))
OUTPUT_RETENTION_SECONDS = int(os.getenv("OUTPUT_RETENTION_SECONDS", str(7 * 24 * 60 * 60)))
OUTPUT_MAX_RUNS = int(os.getenv("OUTPUT_MAX_RUNS", "500"))
OUTPUT_MAX_BYTES = int(os.getenv("OUTPUT_MAX_BYTES", str(20 * 1024 * 1024 * 1024)))
UPLOAD_RETENTION_SECONDS = int(os.getenv("UPLOAD_RETENTION_SECONDS", str(24 * 60 * 60)))
UPLOAD_MAX_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))
ARTIFACT_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ARTIFACT_CLEANUP_INTERVAL_SECONDS", str(60 * 60)))
ACTIVE_WORKFLOW_STALE_SECONDS = int(os.getenv("ACTIVE_WORKFLOW_STALE_SECONDS", str(6 * 60 * 60)))
OPENAI_WORKFLOW_BLOCKING_CONCURRENCY = max(1, int(os.getenv("OPENAI_WORKFLOW_BLOCKING_CONCURRENCY", "4")))
OPENAI_STATUS_URL = "https://status.openai.com/api/v2/status.json"
OPENAI_STATUS_TIMEOUT_SECONDS = float(os.getenv("OPENAI_STATUS_TIMEOUT_SECONDS", "5"))
BATCH_WORKFLOW_CONCURRENCY = max(1, int(os.getenv("BATCH_WORKFLOW_CONCURRENCY", "2")))

# Create directories if they don't exist
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
EVALUATION_DIR.mkdir(parents=True, exist_ok=True)


_load_dotenv(ENV_PATH)

# Refresh environment-derived values after loading the project .env file.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(MAX_UPLOAD_BYTES)))
WORKFLOW_TTL_SECONDS = int(os.getenv("WORKFLOW_TTL_SECONDS", str(WORKFLOW_TTL_SECONDS)))
WORKFLOW_MAX_ENTRIES = int(os.getenv("WORKFLOW_MAX_ENTRIES", str(WORKFLOW_MAX_ENTRIES)))
OUTPUT_RETENTION_SECONDS = int(os.getenv("OUTPUT_RETENTION_SECONDS", str(OUTPUT_RETENTION_SECONDS)))
OUTPUT_MAX_RUNS = int(os.getenv("OUTPUT_MAX_RUNS", str(OUTPUT_MAX_RUNS)))
OUTPUT_MAX_BYTES = int(os.getenv("OUTPUT_MAX_BYTES", str(OUTPUT_MAX_BYTES)))
UPLOAD_RETENTION_SECONDS = int(os.getenv("UPLOAD_RETENTION_SECONDS", str(UPLOAD_RETENTION_SECONDS)))
UPLOAD_MAX_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(UPLOAD_MAX_BYTES)))
ARTIFACT_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ARTIFACT_CLEANUP_INTERVAL_SECONDS", str(ARTIFACT_CLEANUP_INTERVAL_SECONDS)))
ACTIVE_WORKFLOW_STALE_SECONDS = int(os.getenv("ACTIVE_WORKFLOW_STALE_SECONDS", str(ACTIVE_WORKFLOW_STALE_SECONDS)))
OPENAI_WORKFLOW_BLOCKING_CONCURRENCY = max(1, int(os.getenv("OPENAI_WORKFLOW_BLOCKING_CONCURRENCY", str(OPENAI_WORKFLOW_BLOCKING_CONCURRENCY))))
BATCH_WORKFLOW_CONCURRENCY = max(1, int(os.getenv("BATCH_WORKFLOW_CONCURRENCY", str(BATCH_WORKFLOW_CONCURRENCY))))

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

# Initialize FastAPI app
app = FastAPI(
    title="Isomorphic Question Generator API",
    description="Generate isomorphic physics questions using AI agents",
    version="1.0.0",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Health Check Endpoint
# =============================================================================


@app.get("/api/health")
async def health_check():
    """Health check endpoint for debugging connectivity issues."""
    return {
        "status": "healthy",
        "openai_api_key_configured": _has_openai_api_key(),
        "uploads_dir": str(UPLOADS_DIR),
        "uploads_dir_exists": UPLOADS_DIR.exists(),
        "uploads_dir_writable": os.access(UPLOADS_DIR, os.W_OK),
        "outputs_dir": str(OUTPUTS_DIR),
        "outputs_dir_exists": OUTPUTS_DIR.exists(),
        "outputs_dir_writable": os.access(OUTPUTS_DIR, os.W_OK),
    }


@app.get("/api/openai/status")
async def openai_status():
    """
    Return OpenAI public service status.

    Uses OpenAI's public status page API and marks the site as inactive only
    when indicator is major/critical outage.
    """
    try:
        req = Request(
            OPENAI_STATUS_URL,
            headers={"User-Agent": "IsomorphicQuestionGenerator/1.0"},
        )
        with urlopen(req, timeout=OPENAI_STATUS_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))

        status_payload = payload.get("status", {}) if isinstance(payload, dict) else {}
        indicator = str(status_payload.get("indicator", "unknown")).lower()
        description = str(status_payload.get("description", "Unknown"))
        openai_down = indicator in {"major", "critical"}

        return {
            "status": "ok",
            "openai_down": openai_down,
            "indicator": indicator,
            "description": description,
            "checked_at": datetime.now().isoformat(),
        }
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("OpenAI status check failed: %s", exc)
        return {
            "status": "unknown",
            "openai_down": False,
            "indicator": "unknown",
            "description": "Unable to verify OpenAI status right now.",
            "checked_at": datetime.now().isoformat(),
        }


@app.get("/version.json")
async def get_version_json():
    """
    Return version metadata for the frontend footer.

    In production this is typically written during deploy. In local/dev, return
    a stable fallback object so the UI does not generate 404 noise.
    """
    for version_path in VERSION_PATHS:
        if not version_path.exists():
            continue
        try:
            data = json.loads(version_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {"version": "dev", "published_at": None}


def _is_under_directory(path: Path, root: Path) -> bool:
    """Return True when `path` resolves inside `root`."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


# =============================================================================
# In-Memory Storage for Workflows
# =============================================================================

workflows: dict[str, dict] = {}
workflow_tasks: dict[str, asyncio.Task] = {}
batch_jobs: dict[str, dict[str, Any]] = {}
batch_tasks: dict[str, asyncio.Task] = {}

TERMINAL_STATUSES = {"completed", "errored", "cancelled", "lost"}
LOST_STATUS = "lost"
UPLOAD_MANIFEST_FILENAME = "manifest.json"
UPLOAD_SOURCE_EXTENSIONS = ALLOWED_UPLOAD_EXTENSIONS
LATEX_SUFFIXES = {".tex"}
PDF_SUFFIXES = {".pdf"}


def _workflow_now() -> str:
    """Return the server's workflow timestamp string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _workflow_id_for_record(target: dict[str, Any]) -> str:
    """Find a workflow id by object identity."""
    for workflow_id, workflow in workflows.items():
        if workflow is target:
            return workflow_id
    return ""


def _batch_id_for_record(target: dict[str, Any]) -> str:
    """Find a batch id by object identity."""
    for batch_id, batch in batch_jobs.items():
        if batch is target:
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
        workflows[workflow_id] = workflow
    return workflow


def _get_batch_record(batch_id: str) -> dict[str, Any] | None:
    """Return a batch from memory or durable store."""
    batch = batch_jobs.get(batch_id)
    if batch is not None:
        return batch
    batch = workflow_store.load("batch", batch_id)
    if batch is not None:
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
    task.add_done_callback(lambda _task, current_id=workflow_id: workflow_tasks.pop(current_id, None))
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


def _active_output_labels() -> set[str]:
    """Return output labels owned by active in-memory or persisted workflows."""
    labels: set[str] = set()
    records = list(workflows.items()) + workflow_store.list_records("workflow")
    for _workflow_id, workflow in records:
        if workflow.get("status") in TERMINAL_STATUSES:
            continue
        label = workflow.get("outputFolder")
        if isinstance(label, str) and label:
            labels.add(label.split("/", 1)[0])
    return labels


def _cleanup_artifact_directories() -> None:
    """Prune old upload/output folders without touching active workflows."""
    global _last_artifact_cleanup_ts
    now = time.time()
    if now - _last_artifact_cleanup_ts < ARTIFACT_CLEANUP_INTERVAL_SECONDS:
        return
    _last_artifact_cleanup_ts = now

    active_outputs = _active_output_labels()
    output_dirs = [path for path in OUTPUTS_DIR.iterdir() if path.is_dir()]
    output_dirs.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0)
    for path in output_dirs:
        if path.name in active_outputs:
            continue
        try:
            if now - path.stat().st_mtime > OUTPUT_RETENTION_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue

    output_dirs = [path for path in OUTPUTS_DIR.iterdir() if path.is_dir() and path.name not in active_outputs]
    output_dirs.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0)
    while len(output_dirs) > OUTPUT_MAX_RUNS:
        shutil.rmtree(output_dirs.pop(0), ignore_errors=True)
    while sum(_directory_size(path) for path in output_dirs if path.exists()) > OUTPUT_MAX_BYTES and output_dirs:
        shutil.rmtree(output_dirs.pop(0), ignore_errors=True)

    upload_dirs = [path for path in UPLOADS_DIR.iterdir() if path.is_dir()]
    upload_dirs.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0)
    for path in list(upload_dirs):
        try:
            if now - path.stat().st_mtime > UPLOAD_RETENTION_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
    upload_dirs = [path for path in UPLOADS_DIR.iterdir() if path.is_dir()]
    upload_dirs.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0)
    while sum(_directory_size(path) for path in upload_dirs if path.exists()) > UPLOAD_MAX_BYTES and upload_dirs:
        shutil.rmtree(upload_dirs.pop(0), ignore_errors=True)


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
            "normalizer": {"status": "inactive", "data": {}},
            "extracter": {"status": "inactive", "data": {}},
            "verifier": {"status": "inactive", "data": {}},
            "generator": {"status": "inactive", "data": {}},
            "latexifier": {"status": "inactive", "data": {}},
            "evaluation": {"status": "inactive", "data": {}},
            "output": {"status": "inactive", "data": {}},
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


def _workflow_status_payload(workflow_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    """Serialize workflow status for polling and SSE."""
    return {
        "workflowId": workflow_id,
        "status": workflow.get("status", "unknown"),
        "agents": workflow.get("agents", {}),
        "outputFolder": workflow.get("outputFolder", ""),
        "error": workflow.get("error", ""),
        "errorStage": workflow.get("errorStage"),
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
    aliases = {
        "latex_formatter": "latexifier",
        "latex": "latexifier",
        "eval": "evaluation",
    }
    return aliases.get(normalized, normalized)


def _resolve_output_folder_label(label: str) -> Path:
    """Resolve and validate a requested output-folder label.

    Raises:
        HTTPException: If the label is missing, malformed, or points outside the
            outputs directory.
    """
    rel_path = Path((label or "").strip())
    if not rel_path.parts:
        raise HTTPException(status_code=400, detail="sourceOutputFolder is required")
    if rel_path.is_absolute() or any(part in {"", ".", ".."} for part in rel_path.parts):
        raise HTTPException(status_code=400, detail="Invalid sourceOutputFolder")

    output_dir = (OUTPUTS_DIR / rel_path).resolve()
    if not _is_under_directory(output_dir, OUTPUTS_DIR):
        raise HTTPException(status_code=400, detail="Invalid sourceOutputFolder")
    if not output_dir.exists() or not output_dir.is_dir():
        raise HTTPException(status_code=404, detail="Source output folder not found")
    return output_dir


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
    safe_session_id = safe_stem(session_id, fallback="")
    if not safe_session_id or safe_session_id != session_id:
        raise HTTPException(status_code=400, detail="Invalid sessionId")
    session_dir = (UPLOADS_DIR / safe_session_id).resolve()
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


CREATE_DATA_STEP_SEQUENCE: list[tuple[str, str]] = [
    ("normalizer", "Normalizer"),
    ("extracter", "Extracter"),
    ("verifier", "Verifier"),
    ("generator", "Generator"),
    ("latexifier", "Latexifier"),
    ("evaluation", "Evaluator"),
    ("output", "Output"),
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


# =============================================================================
# Agent Creation
# =============================================================================


# =============================================================================
# API Endpoints
# =============================================================================


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files for processing."""
    _prune_workflows()
    logger.info("Upload request received with %d file(s)", len(files))
    uploaded_files = []
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum is {MAX_UPLOAD_FILES}.",
        )

    try:
        # Create a session directory for uploads
        session_id = uuid.uuid4().hex
        session_dir = UPLOADS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"sessionId": session_id, "files": []}
        used_names: set[str] = set()
        logger.info("Created session directory: %s", session_dir)
        
        for file in files:
            if file.filename:
                # Sanitize filename to prevent path traversal
                original_name = Path(file.filename).name
                stored_name = safe_filename(
                    original_name,
                    fallback="source.txt",
                    allowed_suffixes=ALLOWED_UPLOAD_EXTENSIONS,
                )
                extension = Path(stored_name).suffix.lower()
                if extension not in ALLOWED_UPLOAD_EXTENSIONS:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported file type for {original_name}",
                    )
                stored_name = unique_child_name(session_dir, stored_name, used_names)
                file_path = session_dir / stored_name
                file_id = stable_short_hash(f"{session_id}:{stored_name}:{len(used_names)}", 12)
                logger.info("Saving file: %s", stored_name)
                
                try:
                    total_size = 0
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            total_size += len(chunk)
                            if total_size > MAX_UPLOAD_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"File too large: {original_name}. Max size is {MAX_UPLOAD_BYTES} bytes.",
                                )
                            f.write(chunk)
                    item = {
                        "fileId": file_id,
                        "originalName": original_name,
                        "storedName": stored_name,
                        "name": original_name,
                        "size": total_size,
                    }
                    uploaded_files.append(item)
                    manifest["files"].append(item)
                    logger.info("Saved file %s (%d bytes)", stored_name, total_size)
                except Exception as file_error:
                    if file_path.exists():
                        file_path.unlink(missing_ok=True)
                    logger.error("Failed to save file %s: %s", stored_name, file_error)
                    if isinstance(file_error, HTTPException):
                        raise
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to save file {original_name}: {str(file_error)}"
                    )
                finally:
                    await file.close()
        _write_upload_manifest(session_dir, manifest)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload error: %s", e)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    
    return {
        "success": True,
        "sessionId": session_id,
        "files": uploaded_files,
    }


@app.get("/api/dropdown")
async def get_dropdown():
    """Get the prompt dropdown configuration for the frontend."""
    dropdown_path = DATA_DIR / "dropdown.json"
    if not dropdown_path.exists():
        raise HTTPException(status_code=404, detail="Dropdown configuration not found")

    try:
        return json.loads(dropdown_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load dropdown: {str(e)}")


@app.get("/api/rubric")
async def get_rubric():
    """Get the evaluation rubric configuration for the frontend."""
    rubric_path = DATA_DIR / "rubric.json"
    if not rubric_path.exists():
        raise HTTPException(status_code=404, detail="Rubric configuration not found")

    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        if not isinstance(rubric, dict):
            raise ValueError("Rubric root must be a JSON object")
        return rubric
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load rubric: {str(e)}")


@app.get("/api/agents")
async def get_agents():
    """Get the agents configuration for the frontend."""
    agents_path = DATA_DIR / "agents.json"
    if not agents_path.exists():
        raise HTTPException(status_code=404, detail="Agents configuration not found")
    
    try:
        agents = json.loads(agents_path.read_text(encoding="utf-8"))
        rubric_model = _load_rubric_model()

        return {
            "setup": {
                "name": "Starter",
                "description": "Prepares the shared workflow runtime",
                "model": "OpenAI Runtime",
                "reasoning": "none",
                "kind": "runtime",
                "inputFrom": "User",
                "outputTo": "Normalizer",
            },
            **agents,
            "evaluation": {
                "name": "Evaluator",
                "description": "Scores generated outputs and writes evaluator artifacts",
                "model": rubric_model,
                "reasoning": "low",
                "kind": "agent",
                "inputFrom": "Latexifier",
                "outputTo": "Output",
            },
            "output": {
                "name": "Output",
                "description": "Finalizes workflow files and result metadata",
                "model": "Workflow Runtime",
                "reasoning": "none",
                "kind": "runtime",
                "inputFrom": "Evaluator",
                "outputTo": "User",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load agents: {str(e)}")


@app.get("/api/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """Get an agent prompt file content."""
    # Validate prompt name to prevent path traversal
    allowed_prompts = ["normalizer", "extracter", "verifier", "generator", "latexifier", "evaluator"]
    if prompt_name not in allowed_prompts:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    prompt_path = resolve_prompt_path(PROMPTS_DIR, prompt_name)
    if prompt_path is None:
        raise HTTPException(status_code=404, detail="Prompt file not found")
    if not prompt_path.exists():
        raise HTTPException(status_code=404, detail="Prompt file not found")
    
    try:
        return PlainTextResponse(prompt_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load prompt: {str(e)}")


@app.get("/api/evaluation/files")
async def get_evaluation_files():
    """List selectable evaluation files from data/evaluation."""
    files: list[dict[str, Any]] = []
    try:
        for path in _list_evaluation_source_files():
            rel = path.relative_to(EVALUATION_DIR).as_posix()
            files.append(
                {
                    "name": path.name,
                    "relativePath": rel,
                    "size": path.stat().st_size,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load evaluation files: {str(e)}")

    return {"files": files}


@app.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    _prune_workflows()
    _prune_batch_jobs()
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _touch_workflow(workflow)
    return _workflow_status_payload(workflow_id, workflow)


@app.get("/api/workflow/{workflow_id}/result")
async def get_workflow_result(workflow_id: str):
    """Get the final result of a workflow."""
    _prune_workflows()
    _prune_batch_jobs()
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _touch_workflow(workflow)
    if workflow.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Workflow not yet completed")
    
    return {
        "workflowId": workflow_id,
        "result": workflow.get("result"),
        "files": workflow.get("files", []),
        "outputFolder": workflow.get("outputFolder", ""),
    }


@app.get("/api/download/{workflow_id}/{file_path:path}")
async def download_file(workflow_id: str, file_path: str, inline: bool = False):
    """Download a file from a completed workflow."""
    _prune_workflows()
    _prune_batch_jobs()
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _touch_workflow(workflow)
    output_folder = workflow.get("outputFolder")
    if not output_folder:
        raise HTTPException(status_code=404, detail="Output folder not found")
    allowed_names = _downloadable_file_names(workflow)
    if file_path not in allowed_names:
        raise HTTPException(status_code=404, detail="File not found")

    relative_path = Path(file_path)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_dir = (OUTPUTS_DIR / output_folder).resolve()
    resolved_file_path = (output_dir / relative_path).resolve()
    if not _is_under_directory(resolved_file_path, output_dir):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not resolved_file_path.exists() or not resolved_file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    media_type, _ = mimetypes.guess_type(str(resolved_file_path))
    content_disposition_type = "inline" if inline and media_type == "application/pdf" else "attachment"

    return FileResponse(
        path=resolved_file_path,
        filename=relative_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type=content_disposition_type,
    )


@app.post("/api/workflow/start")
async def start_workflow(
    prompt: str = Form(""),
    sessionId: str = Form(""),
    fileIds: str = Form("[]"),
    testFiles: str = Form("[]"),
    runEvaluation: str = Form("false"),
):
    """Start a new workflow and return a stream of status updates."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()
    workflow_id = str(uuid.uuid4())

    file_paths: list[str] = []

    # Parse selected test files from data/evaluation.
    try:
        raw_test_files = json.loads(testFiles) if testFiles else []
    except json.JSONDecodeError:
        raw_test_files = []
    if not isinstance(raw_test_files, list):
        raw_test_files = []
    for rel_path in raw_test_files:
        try:
            rel = Path(str(rel_path))
        except Exception:
            continue
        candidate = (EVALUATION_DIR / rel).resolve()
        if (
            _is_under_directory(candidate, EVALUATION_DIR)
            and candidate.is_file()
            and candidate.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS
        ):
            file_paths.append(str(candidate))
    
    # If sessionId provided, get files from upload directory
    if sessionId:
        try:
            raw_file_ids = json.loads(fileIds) if fileIds else []
        except json.JSONDecodeError:
            raw_file_ids = []
        selected_file_ids = [str(item) for item in raw_file_ids] if isinstance(raw_file_ids, list) else []
        file_paths.extend(_source_paths_from_session(sessionId, selected_file_ids))

    # Deduplicate while preserving order.
    deduped_file_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in file_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        deduped_file_paths.append(path)
    file_paths = deduped_file_paths
    
    # Initialize workflow state
    workflows[workflow_id] = _new_workflow_record()
    _persist_workflow(workflow_id, workflows[workflow_id])
    
    # Start workflow in background
    _register_workflow_task(
        workflow_id,
        run_workflow_async(
            workflow_id,
            prompt,
            file_paths,
            run_evaluation=_parse_form_bool(runEvaluation, default=False),
        ),
    )
    
    return {"success": True, "workflowId": workflow_id}


@app.post("/api/workflow/rerun-from-agent")
async def rerun_workflow_from_agent(
    sourceOutputFolder: str = Form(...),
    startAgent: str = Form(...),
    prompt: str = Form(""),
):
    """Start a new workflow by reusing cached outputs before startAgent."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()

    start_agent = _normalize_agent_key(startAgent)
    if start_agent not in WORKFLOW_AGENT_SEQUENCE[:-1]:
        raise HTTPException(
            status_code=400,
            detail=(
                "startAgent must be one of: "
                + ", ".join(WORKFLOW_AGENT_SEQUENCE[:-1])
            ),
        )

    source_dir = _resolve_output_folder_label(sourceOutputFolder)
    source_label = _relative_output_folder(source_dir)
    file_paths = load_source_file_paths(source_dir)
    if start_agent in {"normalizer", "extracter"} and not file_paths:
        raise HTTPException(
            status_code=400,
            detail=(
                "This source output folder does not include reusable source file paths. "
                "Rerun from verifier or later, or run a fresh workflow."
            ),
        )

    workflow_id = str(uuid.uuid4())
    workflows[workflow_id] = _new_workflow_record()
    _persist_workflow(workflow_id, workflows[workflow_id])
    workflows[workflow_id]["resumedFrom"] = {
        "outputFolder": source_label,
        "startAgent": start_agent,
    }

    _register_workflow_task(
        workflow_id,
        run_workflow_from_agent_async(
            workflow_id,
            prompt,
            file_paths,
            start_agent=start_agent,
            resume_source_dir=source_dir,
            resume_source_label=source_label,
        ),
    )

    return {
        "success": True,
        "workflowId": workflow_id,
        "startAgent": start_agent,
        "sourceOutputFolder": source_label,
    }


@app.post("/api/workflow/start-from-agent-input")
async def start_workflow_from_agent_input(
    startAgent: str = Form(...),
    prompt: str = Form(""),
    agentInput: UploadFile = File(...),
):
    """Start a workflow from one agent using an uploaded source or agent input file."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()

    start_agent = _normalize_agent_key(startAgent)
    if start_agent not in INPUT_TO_AGENT_SEQUENCE:
        raise HTTPException(
            status_code=400,
            detail=(
                "startAgent must be one of: "
                + ", ".join(INPUT_TO_AGENT_SEQUENCE)
            ),
        )

    filename = Path(agentInput.filename or "agent_input.txt").name
    suffix = Path(filename).suffix.lower()
    workflow_id = str(uuid.uuid4())
    content = await agentInput.read()
    await agentInput.close()

    source_file_paths: list[str] = []
    agent_input_payload: Any = None
    input_kind = "agent_input"
    if start_agent in SOURCE_FILE_INPUT_AGENTS:
        source_path = _save_input_to_agent_source_file(
            workflow_id=workflow_id,
            filename=filename,
            content=content,
        )
        source_file_paths.append(str(source_path))
        input_kind = "source_file"
    else:
        if suffix not in ALLOWED_AGENT_INPUT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Agent input file must use one of: "
                    + ", ".join(sorted(ALLOWED_AGENT_INPUT_EXTENSIONS))
                ),
            )
        agent_input_payload = _parse_agent_input_upload(content, filename)
        _validate_input_to_agent_payload(start_agent, agent_input_payload)

    workflows[workflow_id] = _new_workflow_record()
    _persist_workflow(workflow_id, workflows[workflow_id])
    workflows[workflow_id]["inputToAgent"] = {
        "startAgent": start_agent,
        "inputFilename": filename,
        "inputKind": input_kind,
    }

    _register_workflow_task(
        workflow_id,
        run_workflow_from_agent_input_async(
            workflow_id,
            prompt,
            file_paths=source_file_paths,
            start_agent=start_agent,
            agent_input_payload=agent_input_payload,
            input_filename=filename,
        ),
    )

    return {
        "success": True,
        "workflowId": workflow_id,
        "startAgent": start_agent,
        "inputFilename": filename,
        "inputKind": input_kind,
    }


@app.post("/api/create-data/start")
async def start_create_data(prompt: str = Form("")):
    """Start a batch workflow run for every file in data/evaluation."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()

    evaluation_files = _list_evaluation_source_files()
    if not evaluation_files:
        raise HTTPException(status_code=404, detail="No evaluation files found")

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
                "relativePath": path.relative_to(EVALUATION_DIR).as_posix(),
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
        "prompt": (prompt or "").strip(),
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
    _persist_batch(batch_id, batch_jobs[batch_id])

    task = asyncio.create_task(run_create_data_batch_async(batch_id))
    batch_tasks[batch_id] = task
    task.add_done_callback(lambda _task, current_batch_id=batch_id: batch_tasks.pop(current_batch_id, None))

    return {
        "success": True,
        "batchId": batch_id,
        "totalFiles": len(entries),
    }


@app.post("/api/create-data/{batch_id}/stop")
async def stop_create_data(batch_id: str):
    """Stop an active create-data batch run."""
    _prune_workflows()
    _prune_batch_jobs()
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    if batch.get("status") not in TERMINAL_STATUSES:
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

    task = batch_tasks.pop(batch_id, None)
    if task and not task.done():
        task.cancel()

    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "summaryFile": batch.get("summaryFile", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }


@app.get("/api/create-data/{batch_id}/status")
async def get_create_data_status(batch_id: str):
    """Get batch create-data progress."""
    _prune_workflows()
    _prune_batch_jobs()
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    _refresh_batch_progress(batch)
    _touch_batch_job(batch)
    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }


@app.get("/api/create-data/{batch_id}/result")
async def get_create_data_result(batch_id: str):
    """Get the final result of a create-data batch run."""
    _prune_workflows()
    _prune_batch_jobs()
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    _refresh_batch_progress(batch)
    _touch_batch_job(batch)
    if batch.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Batch job not yet completed")

    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "summaryFile": batch.get("summaryFile", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }


@app.get("/api/workflow/{workflow_id}/stream")
async def stream_workflow(workflow_id: str):
    """Stream workflow status updates using SSE."""
    if _get_workflow_record(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    async def generate_events():
        """Generate SSE events for workflow progress."""
        last_status = None
        while True:
            workflow = _get_workflow_record(workflow_id)
            if not workflow:
                break

            # Send current status
            current_status = json.dumps(_workflow_status_payload(workflow_id, workflow))

            if current_status != last_status:
                yield f"data: {current_status}\n\n"
                last_status = current_status
            
            # Check if workflow is done
            if workflow.get("status") in TERMINAL_STATUSES:
                break
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


async def run_workflow_async(
    workflow_id: str,
    prompt: str,
    file_paths: list[str],
    *,
    run_evaluation: bool = True,
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


# =============================================================================
# Batch Workflow Helpers
# =============================================================================


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
) -> None:
    """Run the workflow via the shared engine; surface FastAPI cancel/error states."""
    client = get_openai_client()
    try:
        await run_workflow_async(
            client=client,
            workflow_id=workflow_id,
            workflow=workflow,
            prompt=prompt,
            file_paths=file_paths,
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
        )
    except asyncio.CancelledError:
        logger.info("Workflow %s cancelled", workflow_id)
        _mark_workflow_cancelled(workflow)
        raise
    except Exception as e:
        logger.error("Workflow %s failed: %s", workflow_id, e)
        _mark_workflow_error(workflow, str(e))
        raise


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

# =============================================================================
# Static Files (Serve Frontend)
# =============================================================================

# Mount static files last to catch API routes first
if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        """Return a diagnostic landing page when the frontend has not been built."""
        return HTMLResponse(
            """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Isomorphic Question Generator</title>
  </head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>Frontend not built</h2>
    <p>This FastAPI server is running, but <code>dist/index.html</code> was not found.</p>
    <p>Dev UI: run <code>npm -C frontend run dev:ui</code> (then open the Vite URL).</p>
    <p>Prod UI: run <code>npm -C frontend run build</code>, then refresh this page.</p>
    <p>API health: <a href="/api/health">/api/health</a></p>
  </body>
</html>
            """.strip()
        )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

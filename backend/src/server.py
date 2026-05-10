"""
FastAPI Server for Isomorphic Question Generator

Provides REST API endpoints for:
- File upload
- Workflow execution with SSE streaming
- File downloads
- Static file serving for the frontend
"""

import asyncio
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agents import (
    Agent,
    CodeInterpreterTool,
    ModelSettings,
    Runner,
    RunConfig,
    trace,
)
from agents.logger import logger
from openai import OpenAI
from openai.types.shared.reasoning import Reasoning
try:
    from .artifacts import (
        AGENT_DISPLAY_NAMES,
        AGENT_METADATA_RELATIVE_PATH,
        WORKFLOW_AGENT_SEQUENCE,
        load_agent_input_payload,
        load_agent_output_payload,
        load_source_file_paths,
        to_jsonable,
        write_agent_artifacts,
        write_workflow_metadata,
    )
    from .evaluation import (
        EVALUATION_DATASET_FILENAME,
        EVALUATION_ITEMS_FILENAME,
        EVALUATION_OUTPUTS_DIRNAME,
        EVALUATION_REPORT_FILENAME,
        EVALUATION_RUBRIC_FILENAME,
        EVALUATION_SPREADSHEET_FILENAME,
        run_openai_output_evaluation,
    )
except ImportError:
    from artifacts import (
        AGENT_DISPLAY_NAMES,
        AGENT_METADATA_RELATIVE_PATH,
        WORKFLOW_AGENT_SEQUENCE,
        load_agent_input_payload,
        load_agent_output_payload,
        load_source_file_paths,
        to_jsonable,
        write_agent_artifacts,
        write_workflow_metadata,
    )
    from evaluation import (
        EVALUATION_DATASET_FILENAME,
        EVALUATION_ITEMS_FILENAME,
        EVALUATION_OUTPUTS_DIRNAME,
        EVALUATION_REPORT_FILENAME,
        EVALUATION_RUBRIC_FILENAME,
        EVALUATION_SPREADSHEET_FILENAME,
        run_openai_output_evaluation,
    )
try:
    from .container import SharedWorkflowRuntime, allowed_domains_from_env
    from .schemas import (
        AGENT_INPUT_TYPES,
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        VerifierOutput,
        WorkflowResult,
    )
    from .helpers import (
        apply_requested_difficulty,
        build_json_message,
        load_agents_config,
        load_prompt,
        merge_extracted_metadata_into_config,
        missing_generation_source_ids,
        reconcile_verified_questions,
        resolve_prompt_path,
        upload_file_to_openai,
    )
    from .pipeline_validation import (
        validate_generator_output,
        validate_verifier_output,
    )
    from .safety import (
        create_unique_run_dir,
        ensure_child_path,
        safe_filename,
        safe_stem,
        stable_short_hash,
        unique_child_name,
        write_json,
    )
    from .workflow_payloads import (
        build_evaluation_final_result,
        build_generator_input,
        build_latex_input,
        build_style_review_input,
        generator_requirements,
    )
    from .workflow_store import WorkflowStore
except ImportError:
    from container import SharedWorkflowRuntime, allowed_domains_from_env
    from schemas import (
        AGENT_INPUT_TYPES,
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        VerifierOutput,
        WorkflowResult,
    )
    from helpers import (
        apply_requested_difficulty,
        build_json_message,
        load_agents_config,
        load_prompt,
        merge_extracted_metadata_into_config,
        missing_generation_source_ids,
        reconcile_verified_questions,
        resolve_prompt_path,
        upload_file_to_openai,
    )
    from pipeline_validation import (
        validate_generator_output,
        validate_verifier_output,
    )
    from safety import (
        create_unique_run_dir,
        ensure_child_path,
        safe_filename,
        safe_stem,
        stable_short_hash,
        unique_child_name,
        write_json,
    )
    from workflow_payloads import (
        build_evaluation_final_result,
        build_generator_input,
        build_latex_input,
        build_style_review_input,
        generator_requirements,
    )
    from workflow_store import WorkflowStore

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


BACKEND_ROOT = THIS_DIR.parent if THIS_DIR.name == "src" else THIS_DIR
REPO_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT
DIST_DIR = REPO_ROOT / "dist"
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = BACKEND_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
OUTPUTS_DIR = BACKEND_ROOT / "outputs"
UPLOADS_DIR = BACKEND_ROOT / "uploads"
RUNTIME_DIR = BACKEND_ROOT / "runtime"
EVALUATION_DIR = DATA_DIR / "evaluation"
VERSION_PATHS = [REPO_ROOT / "version.json", DIST_DIR / "version.json"]
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
ALLOWED_AGENT_INPUT_EXTENSIONS = {".json", ".jsonl", ".txt"}
SOURCE_FILE_INPUT_AGENTS = {"normalizer", "extracter"}
INPUT_TO_AGENT_SEQUENCE = [
    "normalizer",
    "extracter",
    "verifier",
    "generator",
    "latex",
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


def _load_dotenv(path: Path) -> None:
    """Load environment variables from .env file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('\'"')
        os.environ.setdefault(key, value)


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
            "latex": {"status": "inactive", "data": {}},
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


def _agent_sequence_index(agent_key: str) -> int:
    """Return the workflow position for an agent key."""
    if agent_key not in WORKFLOW_AGENT_SEQUENCE:
        raise ValueError(f"Unknown agent key: {agent_key}")
    return WORKFLOW_AGENT_SEQUENCE.index(agent_key)


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
        "latex_formatter": "latex",
        "latex": "latex",
        "eval": "evaluation",
    }
    return aliases.get(normalized, normalized)


def _should_run_agent(candidate_key: str, start_agent: str) -> bool:
    """Return True when the candidate is at or after the requested start agent."""
    return _agent_sequence_index(candidate_key) >= _agent_sequence_index(start_agent)


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


def _validate_resume_agent_output(agent_key: str, payload: Any) -> Any:
    """Validate a cached agent payload against the agent's output schema."""
    if payload is None:
        raise ValueError(f"Missing cached output for {agent_key}")

    output_type = AGENT_OUTPUT_TYPES.get(agent_key)
    if output_type is None:
        return payload
    return output_type.model_validate(payload)


def _load_resume_agent_output(resume_source_dir: Path, agent_key: str) -> Any:
    """Load and validate cached output for a resumed workflow step."""
    payload = load_agent_output_payload(resume_source_dir, agent_key)
    return _validate_resume_agent_output(agent_key, payload)


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
    if paths:
        return paths

    # Compatibility fallback for legacy sessions created before manifests.
    return [
        str(f.resolve())
        for f in session_dir.iterdir()
        if f.is_file() and f.name != UPLOAD_MANIFEST_FILENAME and f.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS
    ]


def _file_metadata(name: str, path: Path, *, downloadable: bool = True) -> dict[str, Any]:
    """Build API-facing file metadata without absolute local paths."""
    media_type, _ = mimetypes.guess_type(str(path))
    return {
        "id": stable_short_hash(name),
        "name": name,
        "size": path.stat().st_size if path.exists() else 0,
        "mediaType": media_type or "application/octet-stream",
        "downloadable": downloadable,
    }


def _materialize_latex_output(
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


async def _to_thread_limited(func, /, *args, **kwargs):
    """Run blocking SDK/filesystem work in a bounded thread pool."""
    async with blocking_openai_semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)


def _message_input_texts(value: Any) -> list[str]:
    """Extract input_text strings from an Agents SDK message payload."""
    texts: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "input_text":
                    raw_text = item.get("text")
                    if isinstance(raw_text, str):
                        texts.append(raw_text)
        return texts
    if isinstance(value, list):
        for item in value:
            texts.extend(_message_input_texts(item))
    return texts


def _extract_agent_input_payload(value: Any) -> dict[str, Any]:
    """Extract the structured JSON payload embedded in an uploaded agent input."""
    if isinstance(value, dict):
        if "input" in value and isinstance(value["input"], (dict, list, str)):
            return _extract_agent_input_payload(value["input"])
        structured_keys = (
            "task",
            "configuration",
            "normalizer_config",
            "user_input",
            "defaults",
            "rules",
            "source_questions",
            "verified_questions",
            "generated_questions",
            "isomorphic_questions",
            "questions",
            "document",
            "requirements",
            "final_result",
            "result",
            "rubric",
            "evaluation_items",
            "latex_output",
            "latex_content",
            "latex_file",
            "pdf_file",
        )
        if any(key in value for key in structured_keys):
            return value

    for text in _message_input_texts(value):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _runner_input_from_upload(value: Any) -> Any:
    """Normalize uploaded JSON objects to the message format expected by agents."""
    if isinstance(value, dict):
        return build_json_message(value)
    return value


def _coerce_normalizer_from_agent_input(payload: dict[str, Any]) -> NormalizerOutput:
    """Build NormalizerOutput context from a downstream agent input payload."""
    result = payload.get("final_result") or payload.get("result") or {}
    if isinstance(result, dict):
        config = result.get("config")
        if isinstance(config, dict):
            return NormalizerOutput.model_validate(config)

    for key in ("normalizer_config", "configuration", "config"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return NormalizerOutput.model_validate(candidate)

    document = payload.get("document")
    if isinstance(document, dict):
        return NormalizerOutput(
            title=str(document.get("title") or ""),
            theme=str(document.get("theme") or ""),
        )

    return NormalizerOutput()


def _extract_question_container(candidate: Any) -> dict[str, Any]:
    """Normalize a question list or question container into an Extracter-like dict."""
    if isinstance(candidate, dict):
        if isinstance(candidate.get("questions"), list):
            return candidate
        if isinstance(candidate.get("source_questions"), list):
            return {"questions": candidate.get("source_questions", [])}
    if isinstance(candidate, list):
        return {"questions": candidate}
    return {"questions": []}


def _coerce_extracter_from_agent_input(payload: dict[str, Any]) -> ExtracterOutput:
    """Build ExtracterOutput context from an uploaded downstream input."""
    result = payload.get("final_result") or payload.get("result") or {}
    candidate = result.get("source_questions") if isinstance(result, dict) else None
    if candidate is None:
        candidate = payload.get("source_questions")
    if candidate is None:
        candidate = payload
    data = _extract_question_container(candidate)
    if "filename" not in data:
        data = {"filename": "", **data}
    return ExtracterOutput.model_validate(data)


def _coerce_verifier_from_agent_input(
    payload: dict[str, Any],
    extracter_output: ExtracterOutput,
) -> VerifierOutput:
    """Build VerifierOutput context from an uploaded generator or latex input."""
    result = payload.get("final_result") or payload.get("result") or {}
    candidate = result.get("verified_questions") if isinstance(result, dict) else None
    if candidate is None:
        candidate = payload.get("verified_questions")
    if candidate is None:
        candidate = payload.get("source_questions")
    if candidate is not None:
        data = _extract_question_container(candidate)
        return VerifierOutput.model_validate({"questions": data.get("questions", [])})
    return VerifierOutput(questions=extracter_output.questions)


def _coerce_generator_from_agent_input(payload: dict[str, Any]) -> GeneratorOutput:
    """Build GeneratorOutput context from uploaded downstream agent input."""
    result = payload.get("final_result") or payload.get("result") or {}
    raw_questions = result.get("generated_questions") if isinstance(result, dict) else None
    if raw_questions is None:
        raw_questions = payload.get("generated_questions")
    if raw_questions is None:
        raw_questions = payload.get("isomorphic_questions")
    if raw_questions is not None:
        return GeneratorOutput.model_validate({"isomorphic_questions": raw_questions})

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        raw_questions = []

    items: list[dict[str, Any]] = []
    for index, raw_question in enumerate(raw_questions, start=1):
        if not isinstance(raw_question, dict):
            continue
        item = dict(raw_question)
        item.setdefault("id", index)
        item.setdefault("source_id", str(item.get("source_id") or index))
        item.setdefault("stem", "")
        item.setdefault("options", [])
        item.setdefault("difficulty", "")
        item.setdefault("theme", "")
        item.setdefault("topic_tags", [])
        item.setdefault("generation_notes", "Provided via uploaded Latexifier input.")
        item.setdefault("question_type", "multiple_choice" if item.get("options") else "open_ended")
        item.setdefault("skipped", False)
        item.setdefault("skip_reason", "")
        item.setdefault("answer", "")
        item.setdefault("parts", [])
        items.append(item)

    return GeneratorOutput.model_validate({"isomorphic_questions": items})


def _coerce_latex_from_agent_input(payload: dict[str, Any]) -> LaTeXOutput:
    """Build LaTeXOutput context from uploaded evaluation-stage input."""
    for key in ("latex_output", "latex"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and "latex_content" in candidate:
            return LaTeXOutput.model_validate(candidate)

    result = payload.get("final_result") or payload.get("result") or {}
    latex_content = payload.get("latex_content")
    filename = payload.get("filename") or payload.get("latex_file")
    pdf_filename = payload.get("pdf_filename") or payload.get("pdf_file")
    if isinstance(result, dict):
        latex_content = latex_content or result.get("latex_content")
        filename = filename or result.get("latex_file")
        pdf_filename = pdf_filename or result.get("pdf_file")

    if not isinstance(latex_content, str) or not latex_content.strip():
        raise ValueError(
            "Evaluation input must include latex_content or latex_output.latex_content. "
            "Use a current evaluation agent input artifact, or upload a LaTeX output "
            "artifact and start from the Latexifier."
        )

    return LaTeXOutput(
        latex_content=latex_content,
        filename=str(filename or "isomorphic_questions.tex"),
        pdf_filename=str(pdf_filename or ""),
    )


def _agent_input_has_generated_questions(payload: dict[str, Any]) -> bool:
    """Return True when uploaded evaluation context includes generated questions."""
    result = payload.get("final_result") or payload.get("result") or {}
    raw_questions = result.get("generated_questions") if isinstance(result, dict) else None
    if raw_questions is None:
        raw_questions = payload.get("generated_questions")
    if raw_questions is None:
        raw_questions = payload.get("isomorphic_questions")
    return isinstance(raw_questions, list) and len(raw_questions) > 0


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


def _load_rubric_payload() -> dict[str, Any]:
    """Load the rubric payload included in persisted Evaluation agent inputs."""
    rubric_path = DATA_DIR / "rubric.json"
    if not rubric_path.exists():
        return {"model": _load_rubric_model(), "criteria": []}
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        return rubric if isinstance(rubric, dict) else {"model": _load_rubric_model(), "criteria": []}
    except Exception:
        return {"model": _load_rubric_model(), "criteria": []}


def _mark_provided_agent_state(
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


def _record_provided_agent_artifact(
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


def _mark_reused_agent_state(
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


def _copy_or_record_reused_agent_artifact(
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


def _agent_reasoning_level(agent: Agent) -> Optional[str]:
    """Return the configured reasoning effort for an agent, if any."""
    if agent.model_settings and agent.model_settings.reasoning:
        return agent.model_settings.reasoning.effort
    return None


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


def _load_rubric_model() -> str:
    """Load the rubric model from configuration, falling back to a default."""
    rubric_model = "gpt-5.4-nano"
    rubric_path = DATA_DIR / "rubric.json"
    if not rubric_path.exists():
        return rubric_model
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        return str(rubric.get("model") or rubric_model)
    except Exception:
        return rubric_model


CREATE_DATA_STEP_SEQUENCE: list[tuple[str, str]] = [
    ("normalizer", "Normalizer"),
    ("extracter", "Extracter"),
    ("verifier", "Verifier"),
    ("generator", "Generator"),
    ("latex", "Latexifier"),
    ("evaluation", "Evaluation"),
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


def create_agents(
    *,
    file_ids: Optional[list[str]] = None,
    container_id: Optional[str] = None,
    latex_compile_tool: Any | None = None,
) -> dict[str, Agent]:
    """Create all agents with config from agents.json.

    If container_id is provided, all Code Interpreter tool invocations use the
    same explicit OpenAI container (shared across agent calls in the workflow).
    """
    file_ids = file_ids or []
    agents_config = load_agents_config(AGENTS_CONFIG_PATH, logger)

    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": (
                container_id
                if container_id
                else {
                    "type": "auto",
                    "file_ids": file_ids,
                }
            ),
        }
    )

    agents: dict[str, Agent] = {}
    for agent_key, config in agents_config.items():
        output_type = AGENT_OUTPUT_TYPES.get(agent_key)
        if not output_type:
            logger.warning("No output type defined for agent: %s", agent_key)
            continue

        reasoning_level = config.get("reasoning", "none")
        tools = [code_interpreter] if config.get("useCodeInterpreter", False) else []
        if agent_key == "latex" and latex_compile_tool is not None:
            tools.append(latex_compile_tool)
        valid_reasoning_levels = {"low", "medium", "high", "auto"}
        reasoning = (
            Reasoning(effort=reasoning_level)
            if reasoning_level in valid_reasoning_levels
            else None
        )
        agents[agent_key] = Agent(
            name=config.get("name", agent_key.title()),
            instructions=load_prompt(PROMPTS_DIR, logger, agent_key),
            model=config.get("model", "gpt-4o"),
            tools=tools,
            output_type=output_type,
            model_settings=ModelSettings(
                reasoning=reasoning,
            ),
        )

    missing = [k for k in AGENT_OUTPUT_TYPES.keys() if k not in agents]
    if missing:
        raise ValueError(f"Missing required agent config(s): {', '.join(missing)}")
    return agents


def _any_agent_uses_code_interpreter(agents_config: dict[str, Any]) -> bool:
    """Return True when at least one agent is configured to use code interpreter."""
    for _agent_key, cfg in agents_config.items():
        try:
            if bool(cfg.get("useCodeInterpreter", False)):
                return True
        except Exception:
            continue
    return False


def _container_expires_minutes() -> int:
    """
    OpenAI containers currently enforce a max expiry of 20 minutes
    (relative to last_active_at). Clamp env config to a safe range.
    """
    raw = os.getenv("OPENAI_CONTAINER_EXPIRES_MINUTES", "20")
    try:
        minutes = int(raw)
    except Exception:
        minutes = 20
    if minutes < 1:
        return 1
    if minutes > 20:
        return 20
    return minutes


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
                "name": "Evaluation",
                "description": "Scores generated outputs and writes evaluation artifacts",
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
                "inputFrom": "Evaluation",
                "outputTo": "User",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load agents: {str(e)}")


@app.get("/api/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """Get an agent prompt file content."""
    # Validate prompt name to prevent path traversal
    allowed_prompts = ["normalizer", "extracter", "verifier", "generator", "latex", "python"]
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
    filePaths: str = Form("[]"),
    testFiles: str = Form("[]"),
):
    """Start a new workflow and return a stream of status updates."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()
    workflow_id = str(uuid.uuid4())
    
    # Parse legacy file paths input for compatibility, but do not trust
    # client-provided absolute paths for uploaded source files.
    try:
        raw_file_paths = json.loads(filePaths) if filePaths else []
    except json.JSONDecodeError:
        raw_file_paths = []
    if not isinstance(raw_file_paths, list):
        raw_file_paths = []
    file_paths: list[str] = []
    if raw_file_paths:
        logger.info("Ignoring deprecated client-provided filePaths for workflow %s", workflow_id)

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
    _register_workflow_task(workflow_id, run_workflow_async(workflow_id, prompt, file_paths))
    
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


async def run_workflow_async(workflow_id: str, prompt: str, file_paths: list[str]):
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
) -> None:
    """Run the workflow and write artifacts into a specific output directory."""
    client = get_openai_client()

    try:
        with trace("Isomorphic Question Generation Workflow"):
            if resume_from_agent not in WORKFLOW_AGENT_SEQUENCE:
                raise ValueError(f"Unknown resume_from_agent: {resume_from_agent}")

            outputs_dir.mkdir(parents=True, exist_ok=True)
            workflow["outputFolder"] = output_folder_label
            workflow["status"] = "running"
            _touch_workflow(workflow)

            run_config = RunConfig(
                trace_metadata={
                    "__trace_source__": "isomorphic-question-generator",
                    "timestamp": output_folder_label.replace("/", "_"),
                }
            )
            agent_metadata_records: list[dict[str, Any]] = []
            has_uploaded_agent_input = agent_input_override is not None
            is_input_to_agent_run = input_to_agent_run or has_uploaded_agent_input
            uploaded_agent_input = (
                _runner_input_from_upload(agent_input_override)
                if has_uploaded_agent_input
                else None
            )
            uploaded_agent_payload = (
                _extract_agent_input_payload(agent_input_override)
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

            file_ids = []
            file_names = []
            file_paths_to_upload = (
                file_paths if _should_run_agent("extracter", resume_from_agent) else []
            )
            for file_path in file_paths_to_upload:
                path = Path(file_path)
                if path.exists():
                    file_id = await _to_thread_limited(upload_file_to_openai, client, path, logger)
                    file_ids.append(file_id)
                    file_names.append(path.name)
            if not file_names:
                file_names = [Path(path).name for path in file_paths]

            shared_container_id: Optional[str] = None
            if not hasattr(client, "containers"):
                raise RuntimeError(
                    "OpenAI SDK does not support containers; upgrade openai to >=2.0.0"
                )
            workflow["container"]["status"] = "creating"
            _touch_workflow(workflow)
            container = await _to_thread_limited(
                client.containers.create,
                name=f"isomorphic-workflow-{workflow_id}-{output_folder_label.replace('/', '-')}",
                expires_after={"anchor": "last_active_at", "minutes": _container_expires_minutes()},
                file_ids=file_ids,
            )
            shared_container_id = container.id
            workflow["container"]["status"] = "bootstrapping"
            workflow["container"]["id"] = shared_container_id
            workflow["container"]["allowedDomains"] = allowed_domains_from_env()
            _touch_workflow(workflow)
            shared_runtime = SharedWorkflowRuntime(
                client=client,
                container_id=shared_container_id,
                output_dir=outputs_dir,
                allowed_domains=allowed_domains_from_env(),
            )
            await _to_thread_limited(shared_runtime.bootstrap)
            workflow["container"]["status"] = "active"
            _touch_workflow(workflow)

            agents = create_agents(
                file_ids=file_ids,
                container_id=shared_container_id,
                latex_compile_tool=shared_runtime.build_compile_latex_tool(),
            )

            prompt_text = (prompt or "").strip()
            if _should_run_agent("normalizer", resume_from_agent):
                workflow["agents"]["normalizer"]["status"] = "thinking"
                workflow["agents"]["normalizer"]["data"]["startTime"] = datetime.now().isoformat()
                _touch_workflow(workflow)

                normalizer_input_payload = {
                    "task": "normalize_user_request",
                    "user_input": {
                        "prompt": prompt_text,
                        "files": file_names,
                    },
                    "defaults": {
                        "new_questions_per_question": 1,
                        "difficulty": "",
                        "theme": "",
                        "title": "",
                    },
                    "rules": {
                        "difficulty": (
                            "leave empty unless the user explicitly requests a difficulty target"
                        ),
                        "theme": (
                            "leave empty unless the user requests a target generation theme; "
                            "the extracter determines source theme"
                        ),
                        "title": (
                            "leave empty unless the user requests an output title; "
                            "the extracter determines source title"
                        ),
                    },
                }
                normalizer_input = build_json_message(normalizer_input_payload)
                if has_uploaded_agent_input and resume_from_agent == "normalizer":
                    normalizer_input = uploaded_agent_input
                workflow["agents"]["normalizer"]["data"]["receivedFrom"] = "user"
                workflow["agents"]["normalizer"]["data"]["sentTo"] = "extracter"
                workflow["agents"]["normalizer"]["data"]["input"] = (
                    uploaded_agent_payload
                    if has_uploaded_agent_input and resume_from_agent == "normalizer" and uploaded_agent_payload
                    else {"prompt": prompt_text} if prompt_text else None
                )
                _touch_workflow(workflow)

                start_time = time.time()
                normalizer_result = await Runner.run(
                    agents["normalizer"], input=normalizer_input, run_config=run_config
                )
                elapsed = time.time() - start_time
                normalizer_output: NormalizerOutput = normalizer_result.final_output

                workflow["agents"]["normalizer"]["status"] = "completed"
                workflow["agents"]["normalizer"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["normalizer"]["data"]["executionTime"] = elapsed
                workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="normalizer",
                        agent_name="Normalizer",
                        input_payload=normalizer_input,
                        output_payload=normalizer_output,
                        metadata={
                            "model": agents["normalizer"].model,
                            "reasoning_level": _agent_reasoning_level(agents["normalizer"]),
                            "execution_time_seconds": elapsed,
                            "received_from_node": "user",
                            "sent_to_node": "extracter",
                            "input_summary": f"{len(normalizer_input)} message(s)",
                        },
                    )
                )
            else:
                if is_input_to_agent_run:
                    normalizer_output = _coerce_normalizer_from_agent_input(uploaded_agent_payload)
                    _mark_provided_agent_state(
                        workflow,
                        agent_key="normalizer",
                        agent_output=normalizer_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _record_provided_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="normalizer",
                            agent_output=normalizer_output,
                            input_filename=agent_input_filename,
                            start_agent=resume_from_agent,
                        )
                    )
                elif resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached normalizer output")
                else:
                    normalizer_output = _load_resume_agent_output(
                        resume_source_dir,
                        "normalizer",
                    )
                    _mark_reused_agent_state(
                        workflow,
                        agent_key="normalizer",
                        agent_output=normalizer_output,
                        source_folder=resume_source_label,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _copy_or_record_reused_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="normalizer",
                            agent_output=normalizer_output,
                            resume_source_dir=resume_source_dir,
                            resume_source_label=resume_source_label,
                        )
                    )

            if _should_run_agent("extracter", resume_from_agent):
                workflow["agents"]["extracter"]["status"] = "thinking"
                workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
                _touch_workflow(workflow)

            if _should_run_agent("extracter", resume_from_agent):
                file_refs = [{"type": "input_file", "file_id": fid} for fid in file_ids]
                extracter_input_payload = {
                    "task": "extract_source_questions",
                    "files": file_names,
                    "normalizer_config": normalizer_output.model_dump(),
                    "requirements": {
                        "preserve_math_latex": True,
                        "extract_metadata": ["title", "theme", "author", "date", "course"],
                        "determine_title_from_source": True,
                        "determine_theme_from_extracted_information": True,
                        "must_include_multiple_choice": True,
                        "multiple_choice_option_keys": ["A", "B", "C", "D"],
                        "set_has_explicit_answer_only_if_marked": True,
                        "difficulty_rule": (
                            "leave question difficulty empty unless normalizer_config.difficulty "
                            "is non-empty"
                        ),
                    },
                }
                extracter_input = (
                    uploaded_agent_input
                    if has_uploaded_agent_input and resume_from_agent == "extracter"
                    else build_json_message(extracter_input_payload, file_refs=file_refs)
                )
                workflow["agents"]["extracter"]["data"]["receivedFrom"] = "normalizer"
                workflow["agents"]["extracter"]["data"]["sentTo"] = "verifier"
                workflow["agents"]["extracter"]["data"]["input"] = (
                    uploaded_agent_payload
                    if has_uploaded_agent_input and resume_from_agent == "extracter" and uploaded_agent_payload
                    else extracter_input_payload
                )
                _touch_workflow(workflow)

                start_time = time.time()
                extracter_result = await Runner.run(
                    agents["extracter"], input=extracter_input, run_config=run_config
                )
                extracter_output: ExtracterOutput = extracter_result.final_output
                if file_ids and not extracter_output.questions:
                    retry_extract_payload = {
                        "task": "extract_source_questions_retry",
                        "reason": "previous_result_had_zero_questions",
                        "files": file_names,
                        "normalizer_config": normalizer_output.model_dump(),
                        "requirements": {
                            "must_include_multiple_choice": True,
                            "determine_title_from_source": True,
                            "determine_theme_from_extracted_information": True,
                            "difficulty_rule": (
                                "leave question difficulty empty unless "
                                "normalizer_config.difficulty is non-empty"
                            ),
                        },
                    }
                    retry_extract_input = build_json_message(
                        retry_extract_payload,
                        file_refs=file_refs,
                    )
                    retry_extract_result = await Runner.run(
                        agents["extracter"], input=retry_extract_input, run_config=run_config
                    )
                    extracter_input = retry_extract_input
                    extracter_input_payload = retry_extract_payload
                    extracter_output = retry_extract_result.final_output
                elapsed = time.time() - start_time
                for q in extracter_output.questions:
                    if q.options:
                        q.question_type = "multiple_choice"
                if file_ids and not extracter_output.questions:
                    raise ValueError("Extractor returned zero questions after retry.")
                merge_extracted_metadata_into_config(normalizer_output, extracter_output)
                workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
                logger.info(
                    "Extractor metadata for workflow %s: title=%s theme=%s",
                    workflow_id,
                    extracter_output.title or "",
                    extracter_output.theme or "",
                )

                workflow["agents"]["extracter"]["status"] = "completed"
                workflow["agents"]["extracter"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["extracter"]["data"]["executionTime"] = elapsed
                workflow["agents"]["extracter"]["data"]["output"] = extracter_output.model_dump()
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="extracter",
                        agent_name="Extracter",
                        input_payload=extracter_input,
                        output_payload=extracter_output,
                        metadata={
                            "model": agents["extracter"].model,
                            "reasoning_level": _agent_reasoning_level(agents["extracter"]),
                            "execution_time_seconds": elapsed,
                            "received_from_node": "normalizer",
                            "sent_to_node": "verifier",
                            "input_summary": f"{len(extracter_input)} message(s)",
                            "questions_count": len(extracter_output.questions),
                        },
                    )
                )
            else:
                if is_input_to_agent_run:
                    extracter_output = _coerce_extracter_from_agent_input(uploaded_agent_payload)
                    for q in extracter_output.questions:
                        if q.options:
                            q.question_type = "multiple_choice"
                    merge_extracted_metadata_into_config(normalizer_output, extracter_output)
                    workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
                    _mark_provided_agent_state(
                        workflow,
                        agent_key="extracter",
                        agent_output=extracter_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _record_provided_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="extracter",
                            agent_output=extracter_output,
                            input_filename=agent_input_filename,
                            start_agent=resume_from_agent,
                        )
                    )
                elif resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached extracter output")
                else:
                    extracter_output = _load_resume_agent_output(
                        resume_source_dir,
                        "extracter",
                    )
                    for q in extracter_output.questions:
                        if q.options:
                            q.question_type = "multiple_choice"
                    merge_extracted_metadata_into_config(normalizer_output, extracter_output)
                    workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
                    _mark_reused_agent_state(
                        workflow,
                        agent_key="extracter",
                        agent_output=extracter_output,
                        source_folder=resume_source_label,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _copy_or_record_reused_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="extracter",
                            agent_output=extracter_output,
                            resume_source_dir=resume_source_dir,
                            resume_source_label=resume_source_label,
                        )
                    )

            if _should_run_agent("verifier", resume_from_agent):
                workflow["agents"]["verifier"]["status"] = "thinking"
                workflow["agents"]["verifier"]["data"]["startTime"] = datetime.now().isoformat()
                _touch_workflow(workflow)

            if _should_run_agent("verifier", resume_from_agent):
                verifier_input_payload = {
                    "task": "verify_source_questions",
                    "source_questions": extracter_output.model_dump(),
                    "requirements": {
                        "preserve_all_questions": True,
                        "if_answer_marked_keep_it": True,
                        "if_unmarked_solve_and_mark_correct_option": True,
                        "add_option_explanations": True,
                        "use_code_interpreter_for_calculations": True,
                        "solve_open_ended_and_provide_answer": True,
                        "solve_multi_part_questions_per_part": True,
                    },
                }
                verifier_input = build_json_message(verifier_input_payload)
                if has_uploaded_agent_input and resume_from_agent == "verifier":
                    verifier_input = uploaded_agent_input
                workflow["agents"]["verifier"]["data"]["receivedFrom"] = "extracter"
                workflow["agents"]["verifier"]["data"]["sentTo"] = "generator"
                workflow["agents"]["verifier"]["data"]["input"] = (
                    uploaded_agent_payload
                    if has_uploaded_agent_input and resume_from_agent == "verifier" and uploaded_agent_payload
                    else verifier_input_payload
                )
                _touch_workflow(workflow)

                start_time = time.time()
                verifier_result = await Runner.run(
                    agents["verifier"], input=verifier_input, run_config=run_config
                )
                elapsed = time.time() - start_time
                verifier_output: VerifierOutput = verifier_result.final_output
                verifier_output.questions = reconcile_verified_questions(
                    extracter_output.questions,
                    verifier_output.questions,
                )
                apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
                verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
                if not verifier_report.valid:
                    repair_questions = [
                        q for q in extracter_output.questions
                        if q.source_id in set(verifier_report.repair_source_ids)
                    ]
                    if repair_questions:
                        repair_payload = {
                            **verifier_input_payload,
                            "task": "verify_source_questions_repair",
                            "reason": verifier_report.summary(),
                            "source_questions": {
                                **extracter_output.model_dump(),
                                "questions": [q.model_dump() for q in repair_questions],
                            },
                        }
                        repair_result = await Runner.run(
                            agents["verifier"],
                            input=build_json_message(repair_payload),
                            run_config=run_config,
                        )
                        repair_output: VerifierOutput = repair_result.final_output
                        merged_questions = [
                            q for q in verifier_output.questions
                            if q.source_id not in set(verifier_report.repair_source_ids)
                        ] + repair_output.questions
                        verifier_output = VerifierOutput(questions=merged_questions)
                        verifier_output.questions = reconcile_verified_questions(
                            extracter_output.questions,
                            verifier_output.questions,
                        )
                        apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
                        verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
                    if not verifier_report.valid:
                        raise ValueError(f"Verifier output failed validation: {verifier_report.summary()}")

                workflow["agents"]["verifier"]["status"] = "completed"
                workflow["agents"]["verifier"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["verifier"]["data"]["executionTime"] = elapsed
                workflow["agents"]["verifier"]["data"]["output"] = verifier_output.model_dump()
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="verifier",
                        agent_name="Verifier",
                        input_payload=verifier_input,
                        output_payload=verifier_output,
                        metadata={
                            "model": agents["verifier"].model,
                            "reasoning_level": _agent_reasoning_level(agents["verifier"]),
                            "execution_time_seconds": elapsed,
                            "received_from_node": "extracter",
                            "sent_to_node": "generator",
                            "input_summary": f"{len(verifier_input)} message(s)",
                            "questions_count": len(verifier_output.questions),
                        },
                    )
                )
            else:
                if is_input_to_agent_run:
                    verifier_output = _coerce_verifier_from_agent_input(
                        uploaded_agent_payload,
                        extracter_output,
                    )
                    verifier_output.questions = reconcile_verified_questions(
                        extracter_output.questions,
                        verifier_output.questions,
                    )
                    apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
                    _mark_provided_agent_state(
                        workflow,
                        agent_key="verifier",
                        agent_output=verifier_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _record_provided_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="verifier",
                            agent_output=verifier_output,
                            input_filename=agent_input_filename,
                            start_agent=resume_from_agent,
                        )
                    )
                elif resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached verifier output")
                else:
                    verifier_output = _load_resume_agent_output(
                        resume_source_dir,
                        "verifier",
                    )
                    verifier_output.questions = reconcile_verified_questions(
                        extracter_output.questions,
                        verifier_output.questions,
                    )
                    apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)
                    _mark_reused_agent_state(
                        workflow,
                        agent_key="verifier",
                        agent_output=verifier_output,
                        source_folder=resume_source_label,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _copy_or_record_reused_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="verifier",
                            agent_output=verifier_output,
                            resume_source_dir=resume_source_dir,
                                resume_source_label=resume_source_label,
                            )
                        )

            verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
            if not verifier_report.valid:
                raise ValueError(f"Verifier output failed validation: {verifier_report.summary()}")

            if _should_run_agent("generator", resume_from_agent):
                workflow["agents"]["generator"]["status"] = "thinking"
                workflow["agents"]["generator"]["data"]["startTime"] = datetime.now().isoformat()
                _touch_workflow(workflow)

            if _should_run_agent("generator", resume_from_agent):
                generator_requirements = {
                    "new_questions_per_source": normalizer_output.new_questions_per_question,
                    "fully_original_questions": True,
                    "same_underlying_concept": True,
                    "vary_numerical_values_significantly": True,
                    "physically_reasonable_values": True,
                    "plausible_misconception_distractors": True,
                    "detailed_correct_answer_explanations": True,
                    "explain_each_distractor": True,
                    "latex_math": True,
                    "use_code_interpreter_for_verification": True,
                    "skip_questions_with_figures": True,
                    "generate_open_ended_for_open_ended_sources": True,
                    "open_ended_questions_have_no_options": True,
                    "generate_answers_for_all_open_ended": True,
                    "generate_multi_part_for_multi_part_sources": True,
                    "preserve_source_style_and_verbosity": True,
                    "keep_verbose_open_ended_sources_verbose": True,
                    "do_not_compress_narrative_stems_into_short_directives": True,
                    "do_not_add_padding_to_concise_sources": True,
                    "self_review_style_before_return": True,
                }
                generator_input_payload = {
                    "task": "generate_isomorphic_questions",
                    "configuration": normalizer_output.model_dump(),
                    "source_questions": verifier_output.model_dump(),
                    "requirements": generator_requirements,
                }
                generator_input = build_json_message(generator_input_payload)
                if has_uploaded_agent_input and resume_from_agent == "generator":
                    generator_input_payload = dict(uploaded_agent_payload)
                    uploaded_requirements = generator_input_payload.get("requirements")
                    if not isinstance(uploaded_requirements, dict):
                        uploaded_requirements = {}
                        generator_input_payload["requirements"] = uploaded_requirements
                    uploaded_requirements.setdefault(
                        "preserve_source_style_and_verbosity",
                        True,
                    )
                    uploaded_requirements.setdefault(
                        "keep_verbose_open_ended_sources_verbose",
                        True,
                    )
                    uploaded_requirements.setdefault(
                        "do_not_compress_narrative_stems_into_short_directives",
                        True,
                    )
                    uploaded_requirements.setdefault(
                        "do_not_add_padding_to_concise_sources",
                        True,
                    )
                    uploaded_requirements.setdefault(
                        "self_review_style_before_return",
                        True,
                    )
                    generator_input = build_json_message(generator_input_payload)
                workflow["agents"]["generator"]["data"]["receivedFrom"] = "verifier"
                workflow["agents"]["generator"]["data"]["sentTo"] = "latex"
                workflow["agents"]["generator"]["data"]["input"] = generator_input_payload
                _touch_workflow(workflow)

                start_time = time.time()
                generator_result = await Runner.run(
                    agents["generator"], input=generator_input, run_config=run_config
                )
                generator_output: GeneratorOutput = generator_result.final_output

                missing_source_ids = missing_generation_source_ids(
                    verifier_output.questions,
                    generator_output.isomorphic_questions,
                    normalizer_output.new_questions_per_question,
                )
                if missing_source_ids:
                    retry_questions = [
                        q for q in verifier_output.questions if q.source_id in missing_source_ids
                    ]
                    retry_payload = {
                        "task": "generate_isomorphic_questions_retry",
                        "reason": "missing_required_source_ids",
                        "missing_source_ids": missing_source_ids,
                        "configuration": normalizer_output.model_dump(),
                        "source_questions": {"questions": [q.model_dump() for q in retry_questions]},
                        "requirements": generator_requirements,
                    }
                    retry_input = build_json_message(retry_payload)
                    retry_result = await Runner.run(
                        agents["generator"], input=retry_input, run_config=run_config
                    )
                    retry_output: GeneratorOutput = retry_result.final_output
                    missing_source_ids_set = set(missing_source_ids)
                    generator_output.isomorphic_questions.extend(
                        q for q in retry_output.isomorphic_questions
                        if q.source_id in missing_source_ids_set
                    )
                    missing_source_ids = missing_generation_source_ids(
                        verifier_output.questions,
                        generator_output.isomorphic_questions,
                        normalizer_output.new_questions_per_question,
                    )
                    if missing_source_ids:
                        raise ValueError(
                            "Generator failed to produce required outputs for source_id(s): "
                            + ", ".join(missing_source_ids)
                        )

                style_review_payload = {
                    "task": "review_and_revise_isomorphic_questions_for_style",
                    "configuration": normalizer_output.model_dump(),
                    "source_questions": verifier_output.model_dump(),
                    "draft_isomorphic_questions": generator_output.model_dump(),
                    "requirements": {
                        **generator_requirements,
                        "preserve_ids_and_source_ids": True,
                        "return_complete_final_set": True,
                        "revise_only_when_needed": True,
                        "review_focus": [
                            "Preserve the source question's verbosity and rhetorical style.",
                            "Keep verbose open-ended questions verbose and explanation-oriented.",
                            "Do not compress narrative or multi-sentence source stems into short directives.",
                            "Do not add narrative padding to concise sources.",
                            "Preserve part structure and skip placeholders exactly.",
                        ],
                    },
                }
                style_review_input = build_json_message(style_review_payload)
                style_review_result = await Runner.run(
                    agents["generator"], input=style_review_input, run_config=run_config
                )
                generator_output = style_review_result.final_output
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
                    repair_payload = {
                        "task": "repair_generator_contract_violations",
                        "reason": generator_report.summary(),
                        "configuration": normalizer_output.model_dump(),
                        "source_questions": {
                            "questions": [q.model_dump() for q in repair_questions]
                        },
                        "draft_isomorphic_questions": generator_output.model_dump(),
                        "requirements": {
                            **generator_requirements,
                            "preserve_ids_and_source_ids": True,
                            "return_complete_final_set": True,
                        },
                    }
                    repair_result = await Runner.run(
                        agents["generator"],
                        input=build_json_message(repair_payload),
                        run_config=run_config,
                    )
                    generator_output = repair_result.final_output
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

                workflow["agents"]["generator"]["status"] = "completed"
                workflow["agents"]["generator"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["generator"]["data"]["executionTime"] = elapsed
                workflow["agents"]["generator"]["data"]["output"] = generator_output.model_dump()
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="generator",
                        agent_name="Generator",
                        input_payload=generator_input,
                        output_payload=generator_output,
                        metadata={
                            "model": agents["generator"].model,
                            "reasoning_level": _agent_reasoning_level(agents["generator"]),
                            "execution_time_seconds": elapsed,
                            "received_from_node": "verifier",
                            "sent_to_node": "latex",
                            "input_summary": f"{len(generator_input)} message(s)",
                            "questions_count": len(generator_output.isomorphic_questions),
                        },
                    )
                )
            else:
                if is_input_to_agent_run:
                    generator_output = _coerce_generator_from_agent_input(uploaded_agent_payload)
                    apply_requested_difficulty(
                        generator_output.isomorphic_questions,
                        normalizer_output.difficulty,
                    )
                    _mark_provided_agent_state(
                        workflow,
                        agent_key="generator",
                        agent_output=generator_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _record_provided_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="generator",
                            agent_output=generator_output,
                            input_filename=agent_input_filename,
                            start_agent=resume_from_agent,
                        )
                    )
                elif resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached generator output")
                else:
                    generator_output = _load_resume_agent_output(
                        resume_source_dir,
                        "generator",
                    )
                    apply_requested_difficulty(
                        generator_output.isomorphic_questions,
                        normalizer_output.difficulty,
                    )
                    _mark_reused_agent_state(
                        workflow,
                        agent_key="generator",
                        agent_output=generator_output,
                        source_folder=resume_source_label,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _copy_or_record_reused_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="generator",
                            agent_output=generator_output,
                            resume_source_dir=resume_source_dir,
                                resume_source_label=resume_source_label,
                            )
                        )

            generator_report = validate_generator_output(
                verifier_output.questions,
                generator_output,
                normalizer_output.new_questions_per_question,
            )
            if not generator_report.valid:
                raise ValueError(f"Generator output failed validation: {generator_report.summary()}")

            if _should_run_agent("latex", resume_from_agent):
                workflow["agents"]["latex"]["status"] = "thinking"
                workflow["agents"]["latex"]["data"]["startTime"] = datetime.now().isoformat()
                _touch_workflow(workflow)

            questions_for_latex = []
            for q in generator_output.isomorphic_questions:
                questions_for_latex.append(
                    {
                        "source_id": q.source_id,
                        "stem": q.stem,
                        "options": [opt.model_dump() for opt in q.options],
                        "question_type": q.question_type,
                        "skipped": q.skipped,
                        "skip_reason": q.skip_reason,
                        "answer": q.answer,
                        "parts": [part.model_dump() for part in q.parts],
                    }
                )

            if _should_run_agent("latex", resume_from_agent):
                latex_input_payload = {
                    "task": "format_questions_to_latex",
                    "document": {
                        "title": normalizer_output.title or "Isomorphic Physics Questions",
                        "theme": normalizer_output.theme,
                        "source_title": extracter_output.title,
                        "course": extracter_output.course,
                    },
                    "questions": questions_for_latex,
                    "requirements": {
                        "include_document_preamble": True,
                        "include_macros": [
                            "\\QBlock",
                            "\\Correct",
                            "\\Wrong",
                            "\\OEBlock",
                            "\\MPBlock",
                            "\\MCPart",
                            "\\OEPart",
                            "\\SkipBlock",
                        ],
                        "format_all_questions_with_macros": True,
                        "proper_math_formatting": True,
                        "filename": "isomorphic_questions.tex",
                    },
                }
                latex_input = build_json_message(latex_input_payload)
                if has_uploaded_agent_input and resume_from_agent == "latex":
                    latex_input = uploaded_agent_input
                workflow["agents"]["latex"]["data"]["receivedFrom"] = "generator"
                workflow["agents"]["latex"]["data"]["sentTo"] = "evaluation"
                workflow["agents"]["latex"]["data"]["input"] = (
                    uploaded_agent_payload
                    if has_uploaded_agent_input and resume_from_agent == "latex" and uploaded_agent_payload
                    else latex_input_payload
                )
                _touch_workflow(workflow)

                start_time = time.time()
                latex_result = await Runner.run(
                    agents["latex"], input=latex_input, run_config=run_config
                )
                elapsed = time.time() - start_time
                latex_output: LaTeXOutput = latex_result.final_output
                latex_output, latex_filename, latex_path, pdf_filename, pdf_path = await _to_thread_limited(
                    _materialize_latex_output,
                    latex_output,
                    outputs_dir,
                    shared_runtime,
                )

                workflow["agents"]["latex"]["status"] = "completed"
                workflow["agents"]["latex"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["latex"]["data"]["executionTime"] = elapsed
                workflow["agents"]["latex"]["data"]["output"] = latex_output.model_dump()
                _touch_workflow(workflow)

                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="latex",
                        agent_name="Latexifier",
                        input_payload=latex_input,
                        output_payload=latex_output,
                        metadata={
                            "model": agents["latex"].model,
                            "reasoning_level": _agent_reasoning_level(agents["latex"]),
                            "execution_time_seconds": elapsed,
                            "received_from_node": "generator",
                            "sent_to_node": "evaluation",
                            "input_summary": f"{len(latex_input)} message(s)",
                            "filename": latex_filename,
                            "pdf_filename": pdf_filename,
                        },
                    )
                )
            else:
                if is_input_to_agent_run:
                    latex_output = _coerce_latex_from_agent_input(uploaded_agent_payload)
                    latex_output, latex_filename, latex_path, pdf_filename, pdf_path = await _to_thread_limited(
                        _materialize_latex_output,
                        latex_output,
                        outputs_dir,
                        shared_runtime,
                    )
                    _mark_provided_agent_state(
                        workflow,
                        agent_key="latex",
                        agent_output=latex_output,
                        input_filename=agent_input_filename,
                        start_agent=resume_from_agent,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _record_provided_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="latex",
                            agent_output=latex_output,
                            input_filename=agent_input_filename,
                            start_agent=resume_from_agent,
                        )
                    )
                elif resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached latex output")
                else:
                    latex_output = _load_resume_agent_output(
                        resume_source_dir,
                        "latex",
                    )
                    _mark_reused_agent_state(
                        workflow,
                        agent_key="latex",
                        agent_output=latex_output,
                        source_folder=resume_source_label,
                    )
                    _touch_workflow(workflow)
                    agent_metadata_records.append(
                        _copy_or_record_reused_agent_artifact(
                            outputs_dir=outputs_dir,
                            workflow_id=workflow_id,
                            agent_key="latex",
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
                    latex_path.write_text(latex_output.latex_content, encoding="utf-8")
                    pdf_filename = safe_filename(
                        latex_output.pdf_filename or "isomorphic_questions.pdf",
                        fallback="isomorphic_questions.pdf",
                        allowed_suffixes=PDF_SUFFIXES,
                    )
                    pdf_path = ensure_child_path(outputs_dir, pdf_filename)
                    source_pdf_path = ensure_child_path(resume_source_dir, pdf_filename)
                    if source_pdf_path.exists():
                        shutil.copy2(source_pdf_path, pdf_path)

            if _should_run_agent("evaluation", resume_from_agent):
                workflow["agents"]["evaluation"]["status"] = "thinking"
                workflow["agents"]["evaluation"]["data"]["startTime"] = datetime.now().isoformat()
                workflow["agents"]["evaluation"]["data"]["model"] = _load_rubric_model()
                workflow["agents"]["evaluation"]["data"]["reasoningLevel"] = "low"
                _touch_workflow(workflow)

            if _should_run_agent("evaluation", resume_from_agent):
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
                    "rubric": _load_rubric_payload(),
                }
                if has_uploaded_agent_input and resume_from_agent == "evaluation":
                    evaluation_input = uploaded_agent_input
                workflow["agents"]["evaluation"]["data"]["receivedFrom"] = "latex"
                workflow["agents"]["evaluation"]["data"]["sentTo"] = "output"
                workflow["agents"]["evaluation"]["data"]["input"] = (
                    uploaded_agent_payload
                    if has_uploaded_agent_input and resume_from_agent == "evaluation" and uploaded_agent_payload
                    else f"Evaluate generated questions for workflow {workflow_id}"
                )
                workflow["agents"]["evaluation"]["data"]["output"] = "Running evaluation..."
                _touch_workflow(workflow)

                evaluation_report = await asyncio.to_thread(
                    run_openai_output_evaluation,
                    client=client,
                    final_result=evaluation_final_result,
                        outputs_dir=outputs_dir,
                        latex_path=latex_path,
                        pdf_path=pdf_path,
                        source_files=file_paths,
                        contract_report=contract_report,
                    )
                evaluation_elapsed = time.time() - evaluation_stage_start

                workflow["agents"]["evaluation"]["status"] = "completed"
                workflow["agents"]["evaluation"]["data"]["endTime"] = datetime.now().isoformat()
                workflow["agents"]["evaluation"]["data"]["executionTime"] = evaluation_elapsed
                workflow["agents"]["evaluation"]["data"]["output"] = evaluation_report
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    write_agent_artifacts(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="evaluation",
                        agent_name="Evaluation",
                        input_payload=evaluation_input,
                        output_payload=evaluation_report,
                        metadata={
                            "execution_time_seconds": evaluation_elapsed,
                            "reasoning_level": "low",
                            "received_from_node": "latex",
                            "sent_to_node": "output",
                            "input_summary": (
                                f"Evaluate generated output artifacts for workflow {workflow_id}"
                            ),
                        },
                    )
                )
            else:
                if resume_source_dir is None:
                    raise ValueError("resume_source_dir is required for cached evaluation output")
                evaluation_report = load_agent_output_payload(resume_source_dir, "evaluation")
                if evaluation_report is None:
                    evaluation_report = {}
                _mark_reused_agent_state(
                    workflow,
                    agent_key="evaluation",
                    agent_output=evaluation_report,
                    source_folder=resume_source_label,
                )
                _touch_workflow(workflow)
                agent_metadata_records.append(
                    _copy_or_record_reused_agent_artifact(
                        outputs_dir=outputs_dir,
                        workflow_id=workflow_id,
                        agent_key="evaluation",
                        agent_output=evaluation_report,
                        resume_source_dir=resume_source_dir,
                        resume_source_label=resume_source_label,
                    )
                )

            workflow["agents"]["output"]["status"] = "thinking"
            workflow["agents"]["output"]["data"]["startTime"] = datetime.now().isoformat()
            workflow["agents"]["output"]["data"]["receivedFrom"] = "evaluation"
            workflow["agents"]["output"]["data"]["sentTo"] = "user"
            workflow["agents"]["output"]["data"]["model"] = "Workflow Runtime"
            workflow["agents"]["output"]["data"]["input"] = (
                f"Finalize local output files in outputs/{output_folder_label}"
            )
            workflow["agents"]["output"]["data"]["output"] = "Writing local artifacts..."
            _touch_workflow(workflow)

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
            result_path.write_text(final_result.model_dump_json(indent=2), encoding="utf-8")

            evaluation_outputs_dir = outputs_dir / EVALUATION_OUTPUTS_DIRNAME
            eval_report_path = evaluation_outputs_dir / EVALUATION_REPORT_FILENAME
            eval_dataset_path = evaluation_outputs_dir / EVALUATION_DATASET_FILENAME
            eval_items_path = evaluation_outputs_dir / EVALUATION_ITEMS_FILENAME
            eval_spreadsheet_path = evaluation_outputs_dir / EVALUATION_SPREADSHEET_FILENAME
            eval_rubric_path = evaluation_outputs_dir / EVALUATION_RUBRIC_FILENAME
            files_metadata = [
                _file_metadata("final_result.json", result_path),
                _file_metadata(latex_filename, latex_path),
            ]
            if pdf_path.exists():
                files_metadata.append(_file_metadata(pdf_filename, pdf_path))
            if eval_report_path.exists():
                files_metadata.append(_file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_REPORT_FILENAME}", eval_report_path))
            if eval_dataset_path.exists():
                files_metadata.append(_file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_DATASET_FILENAME}", eval_dataset_path))
            if eval_items_path.exists():
                files_metadata.append(_file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_ITEMS_FILENAME}", eval_items_path))
            if eval_spreadsheet_path.exists():
                files_metadata.append(_file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_SPREADSHEET_FILENAME}", eval_spreadsheet_path))
            if eval_rubric_path.exists():
                files_metadata.append(_file_metadata(f"{EVALUATION_OUTPUTS_DIRNAME}/{EVALUATION_RUBRIC_FILENAME}", eval_rubric_path))

            output_input_artifact = "agent_outputs/output/agent_input.jsonl"
            output_output_artifact = "agent_outputs/output/agent_output.jsonl"
            agent_artifact_file_names = []
            for record in agent_metadata_records:
                for artifact_key in ("input_artifact", "output_artifact"):
                    artifact_name = record.get(artifact_key)
                    if artifact_name:
                        agent_artifact_file_names.append(str(artifact_name))
            agent_artifact_file_names.extend(
                [output_input_artifact, output_output_artifact]
            )
            output_file_names = list(
                dict.fromkeys(
                    [item["name"] for item in files_metadata]
                    + agent_artifact_file_names
                    + [AGENT_METADATA_RELATIVE_PATH]
                )
            )

            output_payload = {
                "agent_name": "Output",
                "execution_time_seconds": None,
                "received_from_node": "evaluation",
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
                    "received_from_node": "evaluation",
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
                        files_metadata.append(_file_metadata(str(artifact_name), artifact_path))
            files_metadata.append(
                _file_metadata(AGENT_METADATA_RELATIVE_PATH, metadata_path)
            )

            workflow["files"] = files_metadata
            workflow["agents"]["output"]["status"] = "completed"
            workflow["agents"]["output"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["output"]["data"]["executionTime"] = output_elapsed
            workflow["agents"]["output"]["data"]["output"] = (
                f"Saved {len(files_metadata)} file(s) to outputs/{output_folder_label}"
            )
            workflow["result"] = final_result.model_dump()
            workflow["status"] = "completed"
            _touch_workflow(workflow)

            logger.info("Workflow %s completed successfully", workflow_id)

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

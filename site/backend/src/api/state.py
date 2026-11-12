"""Shared runtime state, config, and orchestration for the API layer.

This module owns the in-memory workflow/batch registries, the durable
``WorkflowStore``, configuration constants, and the helpers + workflow-runner
wrappers used by the API routers. It deliberately holds no FastAPI route
definitions so routers can import from one place without circular imports.
"""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.logger import logger
from fastapi import HTTPException
from openai import OpenAI
from pydantic import ValidationError

from backend.src.api.upload_budget import upload_disk_budget
from backend.src.artifacts import AGENT_DISPLAY_NAMES, WORKFLOW_AGENT_SEQUENCE
from backend.src.bootstrap import configure_private_umask
from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.cost_controls import (
    download_zip_max_source_bytes,
    max_active_batch_jobs,
    max_create_data_entries,
    max_eval_batch_entries,
    max_eval_batch_items,
    require_within_limit,
)
from backend.src.deployment_maintenance import (
    DeploymentMaintenanceError,
    PaidWorkLease,
    acquire_paid_work_lease,
    ensure_paid_work_allowed,
    try_acquire_artifact_mutation_lease,
)
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
from backend.src.provider_clients import (
    close_agents_sdk_model_provider,
    configure_agents_sdk_model_provider,
    configured_agents_sdk_model_provider,
)
from backend.src.provider_timeouts import (
    await_cancellation_safe_blocking_call,
    openai_transport_options,
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
    safe_exception_category,
    safe_exception_message,
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

configure_private_umask()
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


def _bounded_int_from_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Load a destructive/resource setting strictly within a safe range."""
    raw = (os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer from {minimum} to {maximum}"
        ) from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _bounded_float_from_env(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Load a timeout setting strictly within a safe finite range."""
    raw = (os.getenv(name, str(default)) or "").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a number from {minimum:g} to {maximum:g}"
        ) from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a number from {minimum:g} to {maximum:g}")
    return value


MAX_UPLOAD_BYTES = _bounded_int_from_env(
    "MAX_UPLOAD_BYTES",
    default=20 * 1024 * 1024,
    minimum=1024,
    maximum=1024 * 1024 * 1024,
)

# Total bytes accepted across all files in a single /api/upload request. Bounds a
# request even when it stays under the per-file cap on every file.
MAX_UPLOAD_TOTAL_BYTES = _bounded_int_from_env(
    "MAX_UPLOAD_TOTAL_BYTES",
    default=60 * 1024 * 1024,
    minimum=1024,
    maximum=5 * 1024 * 1024 * 1024,
)

# Leading magic bytes required for common binary upload types. This is a cheap
# declared-type sanity check before bytes reach the extracter's container, not
# full MIME parsing or structural document validation.
UPLOAD_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".bmp": (b"BM",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".jpeg": (b"\xff\xd8\xff",),
    ".jpg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".tif": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".tiff": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
    ".webp": (b"RIFF",),
}


def upload_magic_matches(extension: str, leading_bytes: bytes) -> bool:
    """Return whether a declared binary suffix has its required prefix."""
    normalized = extension.lower()
    signatures = UPLOAD_MAGIC_SIGNATURES.get(normalized)
    if signatures is None:
        return True
    if normalized == ".webp":
        return (
            leading_bytes.startswith(b"RIFF")
            and len(leading_bytes) >= 12
            and leading_bytes[8:12] == b"WEBP"
        )
    return any(leading_bytes.startswith(signature) for signature in signatures)


class SourceFileAdmissionError(ValueError):
    """A local source failed the bounded paid-work admission preflight."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_source_file_paths(
    file_paths: list[str],
    *,
    allowed_extensions: set[str] | frozenset[str] | None = None,
    max_files: int | None = None,
    max_file_bytes: int | None = None,
    max_total_bytes: int | None = None,
) -> list[str]:
    """Resolve, deduplicate, and bound sources before paid work starts."""
    extensions = (
        ALLOWED_UPLOAD_EXTENSIONS
        if allowed_extensions is None
        else {str(extension).lower() for extension in allowed_extensions}
    )
    file_limit = MAX_UPLOAD_FILES if max_files is None else max(1, int(max_files))
    per_file_limit = (
        MAX_UPLOAD_BYTES
        if max_file_bytes is None
        else max(1, int(max_file_bytes))
    )
    total_limit = (
        MAX_UPLOAD_TOTAL_BYTES
        if max_total_bytes is None
        else max(1, int(max_total_bytes))
    )

    resolved: list[str] = []
    seen: set[Path] = set()
    total_bytes = 0
    for raw_path in file_paths:
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
            metadata = path.stat()
        except (OSError, RuntimeError) as exc:
            raise SourceFileAdmissionError(
                "A selected source file was not found."
            ) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceFileAdmissionError(
                "Every selected source must be a regular file."
            )
        if path in seen:
            continue
        seen.add(path)
        if len(resolved) >= file_limit:
            raise SourceFileAdmissionError(
                f"Too many source files. Maximum is {file_limit}."
            )

        suffix = path.suffix.lower()
        if suffix not in extensions:
            raise SourceFileAdmissionError(
                "A selected source file has an unsupported file type."
            )
        if metadata.st_size > per_file_limit:
            raise SourceFileAdmissionError(
                f"A source file exceeds {per_file_limit} bytes.",
                status_code=413,
            )
        total_bytes += metadata.st_size
        if total_bytes > total_limit:
            raise SourceFileAdmissionError(
                f"Selected source files exceed {total_limit} bytes in total.",
                status_code=413,
            )
        try:
            with path.open("rb") as source:
                leading_bytes = source.read(1024)
        except OSError as exc:
            raise SourceFileAdmissionError(
                "A selected source file could not be read."
            ) from exc
        if not upload_magic_matches(suffix, leading_bytes):
            raise SourceFileAdmissionError(
                f"A selected source does not appear to be a valid {suffix} file."
            )
        resolved.append(str(path))

    if not resolved:
        raise SourceFileAdmissionError("Select at least one source file.")
    return resolved


WORKFLOW_TTL_SECONDS = _bounded_int_from_env(
    "WORKFLOW_TTL_SECONDS",
    default=24 * 60 * 60,
    minimum=60,
    maximum=30 * 24 * 60 * 60,
)

WORKFLOW_MAX_ENTRIES = _bounded_int_from_env(
    "WORKFLOW_MAX_ENTRIES",
    default=200,
    minimum=1,
    maximum=10_000,
)

# Paid-start response mappings outlive ordinary transient network failures and
# browser restarts without becoming an unbounded permanent ledger.
IDEMPOTENCY_RETENTION_SECONDS = 7 * 24 * 60 * 60

OUTPUT_RETENTION_SECONDS = _bounded_int_from_env(
    "OUTPUT_RETENTION_SECONDS",
    default=7 * 24 * 60 * 60,
    minimum=60 * 60,
    maximum=365 * 24 * 60 * 60,
)

OUTPUT_MAX_RUNS = _bounded_int_from_env(
    "OUTPUT_MAX_RUNS",
    default=500,
    minimum=1,
    maximum=100_000,
)

OUTPUT_MAX_BYTES = _bounded_int_from_env(
    "OUTPUT_MAX_BYTES",
    default=20 * 1024 * 1024 * 1024,
    minimum=1024 * 1024,
    maximum=10 * 1024 * 1024 * 1024 * 1024,
)

UPLOAD_RETENTION_SECONDS = _bounded_int_from_env(
    "UPLOAD_RETENTION_SECONDS",
    default=24 * 60 * 60,
    minimum=60 * 60,
    maximum=365 * 24 * 60 * 60,
)

UPLOAD_MAX_BYTES = _bounded_int_from_env(
    "UPLOAD_MAX_BYTES",
    default=2 * 1024 * 1024 * 1024,
    minimum=1024 * 1024,
    maximum=10 * 1024 * 1024 * 1024 * 1024,
)

ARTIFACT_CLEANUP_INTERVAL_SECONDS = _bounded_int_from_env(
    "ARTIFACT_CLEANUP_INTERVAL_SECONDS",
    default=60 * 60,
    minimum=60,
    maximum=24 * 60 * 60,
)

PRUNE_INTERVAL_SECONDS = _bounded_int_from_env(
    "PRUNE_INTERVAL_SECONDS",
    default=60,
    minimum=5,
    maximum=60 * 60,
)

ACTIVE_WORKFLOW_STALE_SECONDS = _bounded_int_from_env(
    "ACTIVE_WORKFLOW_STALE_SECONDS",
    default=6 * 60 * 60,
    minimum=60,
    maximum=30 * 24 * 60 * 60,
)

OPENAI_WORKFLOW_BLOCKING_CONCURRENCY = _bounded_int_from_env(
    "OPENAI_WORKFLOW_BLOCKING_CONCURRENCY",
    default=4,
    minimum=1,
    maximum=64,
)

OPENAI_STATUS_URL = "https://status.openai.com/api/v2/status.json"

OPENAI_STATUS_TIMEOUT_SECONDS = _bounded_float_from_env(
    "OPENAI_STATUS_TIMEOUT_SECONDS",
    default=5,
    minimum=1,
    maximum=60,
)

BATCH_WORKFLOW_CONCURRENCY = _bounded_int_from_env(
    "BATCH_WORKFLOW_CONCURRENCY",
    default=2,
    minimum=1,
    maximum=32,
)
MAX_ACTIVE_BATCH_JOBS = max_active_batch_jobs()

# Process-wide cap for full agentic workflows. The Raspberry Pi handoff defaults
# to one expensive workflow at a time even when multiple direct or batch starts
# arrive concurrently.
OPENAI_WORKFLOW_CONCURRENCY = _bounded_int_from_env(
    "OPENAI_WORKFLOW_CONCURRENCY",
    default=1,
    minimum=1,
    maximum=32,
)

# Admission cap for process-local, non-terminal workflow records. This prevents
# a burst of starts from creating an unbounded queue behind the execution cap.
MAX_ACTIVE_WORKFLOWS = _bounded_int_from_env(
    "MAX_ACTIVE_WORKFLOWS",
    default=4,
    minimum=1,
    maximum=1000,
)

# The current frontend polls workflow status and does not consume token deltas.
# Keep the legacy SSE token path available for explicit integrations without
# paying its per-run queue and streaming overhead by default.
WORKFLOW_TOKEN_STREAMING_ENABLED = (
    (os.getenv("WORKFLOW_TOKEN_STREAMING_ENABLED", "false") or "").strip().lower()
    in {"1", "true", "yes", "on"}
)

def _ensure_private_runtime_directory(path: Path) -> None:
    """Create or repair one mutable data root to owner-only permissions."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


for _private_runtime_directory in (UPLOADS_DIR, OUTPUTS_DIR, RUNTIME_DIR):
    _ensure_private_runtime_directory(_private_runtime_directory)

EVALUATION_DIR.mkdir(parents=True, exist_ok=True)

_openai_client: Optional[OpenAI] = None

workflow_store = WorkflowStore(RUNTIME_DIR / "workflows.sqlite3")

blocking_openai_semaphore = asyncio.Semaphore(OPENAI_WORKFLOW_BLOCKING_CONCURRENCY)

workflow_execution_semaphore = asyncio.Semaphore(OPENAI_WORKFLOW_CONCURRENCY)

batch_entry_semaphore = asyncio.Semaphore(BATCH_WORKFLOW_CONCURRENCY)

# Workflow starts, Create Data workflow pools, and batch starts share one
# process-local admission lock. Reservations cover the async gap between a
# request being accepted and its paid background work becoming visible in the
# workflow registry.
workflow_admission_lock = asyncio.Lock()
batch_admission_lock = workflow_admission_lock
workflow_admission_reservations: dict[str, int] = {}
workflow_admission_owners: dict[str, str] = {}
paid_work_leases: dict[str, PaidWorkLease] = {}

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
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if api_key:
            configure_agents_sdk_model_provider(api_key)
        return _openai_client

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. "
            "Set it in the environment or in site/.env.local "
            "(copy site/.env.example to create that file)."
        )

    client = OpenAI(
        api_key=api_key,
        **openai_transport_options(),
    )
    configure_agents_sdk_model_provider(api_key)
    _openai_client = client
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


_ZIP_SNAPSHOT_COMMENT_PREFIX = b"uconn-output-snapshot-v1:"


def _workflow_zip_source_inventory(
    output_dir: Path,
    *,
    excluded_paths: set[Path] | None = None,
) -> tuple[list[Path], list[tuple[str, int, int, int]]]:
    """Return safe ZIP sources plus metadata used to detect stale archives."""
    root = output_dir.resolve()
    excluded = {path.resolve() for path in (excluded_paths or set())}
    source_paths: list[Path] = []
    snapshot: list[tuple[str, int, int, int]] = []
    source_bytes = 0
    source_byte_limit = download_zip_max_source_bytes()
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() == ".zip":
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if resolved in excluded:
                continue
            stat = path.stat()
            relative = path.relative_to(output_dir).as_posix()
        except (OSError, ValueError):
            continue
        source_bytes += stat.st_size
        if source_bytes > source_byte_limit:
            raise ValueError(
                "Workflow output is too large to archive safely "
                f"({source_bytes} bytes)"
            )
        source_paths.append(path)
        snapshot.append(
            (
                relative,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
        )
    return source_paths, snapshot


def _workflow_zip_snapshot_digest(
    snapshot: list[tuple[str, int, int, int]],
) -> str:
    """Hash a deterministic artifact snapshot for ZIP freshness checks."""
    payload = json.dumps(
        snapshot,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _workflow_zip_is_current(output_dir: Path, zip_path: Path) -> bool:
    """Return whether an archive exactly matches the current artifact tree."""
    if not zip_path.is_file():
        return False
    try:
        _paths, snapshot = _workflow_zip_source_inventory(output_dir)
        expected_comment = (
            _ZIP_SNAPSHOT_COMMENT_PREFIX
            + _workflow_zip_snapshot_digest(snapshot).encode("ascii")
        )
        with zipfile.ZipFile(zip_path) as archive:
            return archive.comment == expected_comment
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def _build_workflow_zip(output_dir: Path, zip_path: Path) -> None:
    """
    Zip every file in a workflow output folder into `zip_path`.

    Existing archives (and the in-progress temp file) are excluded so a rebuilt
    zip never nests an older one. The archive is written to a temp name and
    renamed into place so a concurrent download can't see a partial file.
    """
    source_paths, initial_snapshot = _workflow_zip_source_inventory(output_dir)
    snapshot_digest = _workflow_zip_snapshot_digest(initial_snapshot)

    temporary_handle = tempfile.NamedTemporaryFile(
        prefix=f".{zip_path.name}.",
        suffix=".tmp",
        dir=output_dir,
        delete=False,
    )
    tmp_path = Path(temporary_handle.name)
    temporary_handle.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source_paths:
                archive.write(path, path.relative_to(output_dir).as_posix())
            archive.comment = (
                _ZIP_SNAPSHOT_COMMENT_PREFIX
                + snapshot_digest.encode("ascii")
            )
        _paths, final_snapshot = _workflow_zip_source_inventory(
            output_dir,
            excluded_paths={tmp_path},
        )
        if final_snapshot != initial_snapshot:
            raise RuntimeError("Workflow artifacts changed while the archive was built")
        tmp_path.replace(zip_path)
    finally:
        tmp_path.unlink(missing_ok=True)

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

EVAL_BATCH_RECORD_KIND = "eval_batch"

UPLOAD_MANIFEST_FILENAME = "manifest.json"


def _task_is_unfinished(
    tasks: dict[str, asyncio.Task],
    record_id: str,
) -> bool:
    """Return whether a tracked task still owns runtime work for a record."""
    task = tasks.get(record_id)
    return task is not None and not task.done()


def _active_record_or_task_ids(
    records: dict[str, dict[str, Any]],
    tasks: dict[str, asyncio.Task],
) -> set[str]:
    """Return nonterminal record IDs plus every unfinished tracked task ID."""
    return {
        record_id
        for record_id, record in records.items()
        if record.get("status") not in TERMINAL_STATUSES
    } | {
        record_id
        for record_id, task in tasks.items()
        if not task.done()
    }


def _workflow_now() -> str:
    """Return the server's workflow timestamp string."""
    return utc_now_iso()


def _timestamp_seconds(value: Any) -> float | None:
    """Parse an ISO-8601 timestamp without discarding its UTC offset."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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


def _discard_workflow_record(workflow_id: str) -> None:
    """Remove a workflow that failed before its background task was registered."""
    workflows.pop(workflow_id, None)
    workflow_tasks.pop(workflow_id, None)
    workflow_admission_owners.pop(workflow_id, None)
    workflow_store.delete("workflow", workflow_id)


def _discard_batch_record(batch_id: str) -> None:
    """Remove a Create Data batch that failed before its task was registered."""
    batch_jobs.pop(batch_id, None)
    batch_tasks.pop(batch_id, None)
    workflow_store.delete("batch", batch_id)


def _persist_batch(batch_id: str, batch: dict[str, Any]) -> None:
    """Persist a batch record to SQLite."""
    if batch_id:
        workflow_store.save("batch", batch_id, batch)


def _persist_eval_batch(batch_id: str, batch: dict[str, Any]) -> None:
    """Persist an eval-batch record separately from create-data batches."""
    if batch_id:
        workflow_store.save(EVAL_BATCH_RECORD_KIND, batch_id, batch)


def _discard_eval_batch_record(batch_id: str) -> None:
    """Remove an eval batch that failed before its task was registered."""
    eval_batch_jobs.pop(batch_id, None)
    eval_batch_tasks.pop(batch_id, None)
    workflow_store.delete(EVAL_BATCH_RECORD_KIND, batch_id)


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
        reservation_id = workflow_admission_owners.get(current_id)
        if reservation_id:
            # A task cancelled before its coroutine's first step never enters
            # the wrapper's ``finally``. This idempotent fallback prevents that
            # narrow scheduling race from leaking paid-work capacity.
            asyncio.create_task(_release_workflow_reservation(reservation_id))
        if done_task.cancelled():
            return
        # The failure is already recorded on the workflow record and logged by
        # _execute_workflow. Retrieve the exception here so asyncio doesn't emit
        # a spurious "Task exception was never retrieved" traceback for this
        # fire-and-forget background task.
        exc = done_task.exception()
        if exc is not None:
            logger.debug(
                "Workflow %s task finished with error (%s)",
                current_id,
                safe_exception_category(exc),
            )

    task.add_done_callback(_on_task_done)
    return task


def _ensure_workflow_capacity(required: int = 1) -> None:
    """Reject work exceeding active plus process-local reserved workflow slots."""
    requested = max(1, int(required))
    unreserved_active = sum(
        1
        for workflow_id, workflow in workflows.items()
        if workflow.get("status") not in TERMINAL_STATUSES
        and workflow_admission_owners.get(workflow_id)
        not in workflow_admission_reservations
    )
    reserved = sum(workflow_admission_reservations.values())
    if unreserved_active + reserved + requested > MAX_ACTIVE_WORKFLOWS:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many active workflows. "
                f"Maximum is {MAX_ACTIVE_WORKFLOWS}; wait for an active run to finish."
            ),
        )


def _reserve_workflow_capacity_locked(
    reservation_id: str,
    required: int,
) -> None:
    """Create one workflow reservation while ``workflow_admission_lock`` is held."""
    if reservation_id in workflow_admission_reservations:
        raise RuntimeError("Workflow admission reservation already exists")
    lease = _acquire_paid_work_lease_locked(reservation_id)
    try:
        requested = max(1, int(required))
        _ensure_workflow_capacity(requested)
        workflow_admission_reservations[reservation_id] = requested
    except BaseException:
        paid_work_leases.pop(reservation_id, None)
        lease.close()
        raise


def _acquire_paid_work_lease_locked(owner_id: str) -> PaidWorkLease:
    """Acquire one cross-process shared activity lease under admission lock."""
    if owner_id in paid_work_leases:
        raise RuntimeError("Paid-work activity lease already exists")
    try:
        lease = acquire_paid_work_lease()
    except DeploymentMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    paid_work_leases[owner_id] = lease
    return lease


async def _acquire_paid_work_lease(owner_id: str) -> None:
    """Acquire a paid-work activity lease without reserving workflow capacity."""
    async with workflow_admission_lock:
        _acquire_paid_work_lease_locked(owner_id)


async def _release_paid_work_lease(owner_id: str) -> None:
    """Release one paid-work activity lease after all cleanup has unwound."""
    async with workflow_admission_lock:
        lease = paid_work_leases.pop(owner_id, None)
        if lease is not None:
            lease.close()


async def _reserve_workflow_capacity(
    reservation_id: str,
    required: int = 1,
) -> None:
    """Atomically reserve workflow capacity across an awaited request path."""
    async with workflow_admission_lock:
        _reserve_workflow_capacity_locked(reservation_id, required)


async def _ensure_workflow_reservation(
    reservation_id: str,
    required: int,
) -> None:
    """Ensure an identical reservation exists, creating it atomically if absent."""
    requested = max(1, int(required))
    async with workflow_admission_lock:
        existing = workflow_admission_reservations.get(reservation_id)
        if existing is None:
            _reserve_workflow_capacity_locked(reservation_id, requested)
        elif existing != requested:
            raise RuntimeError("Workflow admission reservation size changed")
        elif reservation_id not in paid_work_leases:
            _acquire_paid_work_lease_locked(reservation_id)


def _release_workflow_reservation_locked(reservation_id: str) -> None:
    """Release one reservation while the caller owns admission serialization."""
    workflow_admission_reservations.pop(reservation_id, None)
    owned_ids = [
        workflow_id
        for workflow_id, owner in workflow_admission_owners.items()
        if owner == reservation_id
    ]
    for workflow_id in owned_ids:
        workflow_admission_owners.pop(workflow_id, None)
    lease = paid_work_leases.pop(reservation_id, None)
    if lease is not None:
        lease.close()


async def _release_workflow_reservation(reservation_id: str) -> None:
    """Release a workflow reservation and all process-local ownership markers."""
    async with workflow_admission_lock:
        _release_workflow_reservation_locked(reservation_id)


async def _admit_workflow_record(
    workflow_id: str,
    workflow: dict[str, Any],
    *,
    reservation_id: str,
) -> None:
    """Atomically convert a reserved slot into a persisted workflow record."""
    async with workflow_admission_lock:
        try:
            ensure_paid_work_allowed()
        except DeploymentMaintenanceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        quota = workflow_admission_reservations.get(reservation_id)
        if quota is None:
            raise RuntimeError("Workflow admission reservation is missing")
        if reservation_id not in paid_work_leases:
            raise RuntimeError("Paid-work activity lease is missing")
        active_for_owner = sum(
            1
            for current_id, owner in workflow_admission_owners.items()
            if owner == reservation_id
            and workflows.get(current_id, {}).get("status") not in TERMINAL_STATUSES
        )
        if active_for_owner >= quota:
            raise HTTPException(
                status_code=429,
                detail="Reserved workflow concurrency is already in use.",
            )
        if workflow_id in workflows:
            raise RuntimeError("Workflow record already exists")

        workflows[workflow_id] = workflow
        workflow_admission_owners[workflow_id] = reservation_id
        try:
            _persist_workflow(workflow_id, workflow)
        except BaseException:
            workflow_admission_owners.pop(workflow_id, None)
            workflows.pop(workflow_id, None)
            raise


async def _admit_create_data_batch(
    batch_id: str,
    batch: dict[str, Any],
    *,
    reservation_id: str,
    workflow_slots: int,
) -> None:
    """Atomically reserve paid child slots and persist a Create Data batch."""
    async with workflow_admission_lock:
        _ensure_batch_capacity()
        _reserve_workflow_capacity_locked(reservation_id, workflow_slots)
        batch["workflowAdmissionReservationId"] = reservation_id
        batch["workflowAdmissionSlots"] = max(1, int(workflow_slots))
        batch_jobs[batch_id] = batch
        try:
            _persist_batch(batch_id, batch)
        except BaseException:
            batch_jobs.pop(batch_id, None)
            _release_workflow_reservation_locked(reservation_id)
            raise


async def _run_with_workflow_reservation(
    reservation_id: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """Run paid workflow work and release its slot after all cleanup unwinds."""
    try:
        return await operation()
    finally:
        await _release_workflow_reservation(reservation_id)


def _ensure_batch_capacity() -> None:
    """Reject starts that would multiply expensive background batch work."""
    try:
        ensure_paid_work_allowed()
    except DeploymentMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    active = sum(
        len(_active_record_or_task_ids(records, tasks))
        for records, tasks in (
            (batch_jobs, batch_tasks),
            (eval_batch_jobs, eval_batch_tasks),
        )
    )
    if active >= MAX_ACTIVE_BATCH_JOBS:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many active batch jobs. "
                f"Maximum is {MAX_ACTIVE_BATCH_JOBS}; wait for the active batch to finish."
            ),
        )


def _terminalize_lost_workflow(workflow: dict[str, Any], reason: str) -> None:
    """Mark a processless workflow and its active node consistently terminal."""
    workflow["status"] = LOST_STATUS
    workflow["error"] = reason
    workflow["errorStage"] = _active_agent_key(workflow)
    now = _workflow_now()
    workflow["updated_at"] = now
    for agent in workflow.get("agents", {}).values():
        if not isinstance(agent, dict) or agent.get("status") != "thinking":
            continue
        agent["status"] = "errored"
        data = agent.setdefault("data", {})
        data["error"] = reason
        data.setdefault("endTime", now)


def _release_orphaned_workflow_reservation(workflow_id: str) -> None:
    """Release a reservation only when it has no other active owner."""
    reservation_id = workflow_admission_owners.get(workflow_id)
    if not reservation_id:
        return
    other_active_workflow = any(
        current_id != workflow_id
        and owner == reservation_id
        and workflows.get(current_id, {}).get("status") not in TERMINAL_STATUSES
        for current_id, owner in workflow_admission_owners.items()
    )
    active_batch_owner = any(
        str(batch.get("workflowAdmissionReservationId") or "") == reservation_id
        and (
            batch.get("status") not in TERMINAL_STATUSES
            or _task_is_unfinished(batch_tasks, batch_id)
        )
        for batch_id, batch in batch_jobs.items()
    )
    if not other_active_workflow and not active_batch_owner:
        _release_workflow_reservation_locked(reservation_id)


def _terminalize_lost_batch(
    batch: dict[str, Any],
    *,
    reason: str,
    refresh: Callable[[dict[str, Any]], None],
) -> None:
    """Mark a processless batch and every unfinished entry terminal."""
    batch["status"] = LOST_STATUS
    batch["error"] = reason
    batch["updated_at"] = _workflow_now()
    for entry in batch.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") in TERMINAL_STATUSES:
            continue
        entry["status"] = "errored"
        entry["error"] = reason
    refresh(batch)


def _workflow_has_unfinished_owner(
    workflow_id: str,
    workflow: dict[str, Any],
) -> bool:
    if _task_is_unfinished(workflow_tasks, workflow_id):
        return True
    parent_batch_id = str(workflow.get("parentBatchId") or "")
    return bool(
        parent_batch_id
        and _task_is_unfinished(batch_tasks, parent_batch_id)
    )


def _prune_workflows() -> None:
    """Bound memory by removing expired/old workflow records."""
    now = datetime.now(timezone.utc).timestamp()
    expired_ids: list[str] = []
    for workflow_id, workflow in workflows.items():
        updated_ts = _timestamp_seconds(workflow.get("updated_at"))
        if updated_ts is None:
            continue

        status = workflow.get("status")
        if status not in TERMINAL_STATUSES:
            if (
                not _workflow_has_unfinished_owner(workflow_id, workflow)
                and now - updated_ts > ACTIVE_WORKFLOW_STALE_SECONDS
            ):
                _terminalize_lost_workflow(
                    workflow,
                    "Workflow heartbeat was lost before completion.",
                )
                _persist_workflow(workflow_id, workflow)
                _release_orphaned_workflow_reservation(workflow_id)
            continue

        if (
            not _workflow_has_unfinished_owner(workflow_id, workflow)
            and now - updated_ts > WORKFLOW_TTL_SECONDS
        ):
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
        if status in TERMINAL_STATUSES and not _workflow_has_unfinished_owner(
            workflow_id, workflow
        ):
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
        unfinished_task = _task_is_unfinished(batch_tasks, batch_id)
        updated_ts = _timestamp_seconds(batch.get("updated_at"))
        if (
            batch.get("status") not in TERMINAL_STATUSES
            and not unfinished_task
            and updated_ts is not None
            and now - updated_ts > ACTIVE_WORKFLOW_STALE_SECONDS
        ):
            reason = "Create-data batch heartbeat was lost before completion."
            _terminalize_lost_batch(
                batch,
                reason=reason,
                refresh=_refresh_batch_progress,
            )
            for entry in batch.get("entries", []):
                workflow_id = str(entry.get("workflowId") or "")
                workflow = workflows.get(workflow_id)
                if (
                    workflow_id
                    and workflow is not None
                    and workflow.get("status") not in TERMINAL_STATUSES
                ):
                    _terminalize_lost_workflow(workflow, reason)
                    _persist_workflow(workflow_id, workflow)
            _persist_batch(batch_id, batch)
            reservation_id = str(
                batch.get("workflowAdmissionReservationId") or ""
            )
            if reservation_id:
                _release_workflow_reservation_locked(reservation_id)
            continue
        if batch.get("status") not in TERMINAL_STATUSES or unfinished_task:
            continue
        if updated_ts is None:
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
        if status in TERMINAL_STATUSES and not _task_is_unfinished(
            batch_tasks,
            batch_id,
        ):
            batch_jobs.pop(batch_id, None)
            workflow_store.delete("batch", batch_id)
            task = batch_tasks.pop(batch_id, None)
            if task and not task.done():
                task.cancel()
        if len(batch_jobs) <= WORKFLOW_MAX_ENTRIES:
            break

def _is_old(record: dict, now: float) -> bool:
    ts = _timestamp_seconds(record.get("updated_at"))
    return ts is not None and now - ts > WORKFLOW_TTL_SECONDS


def _prune_eval_batch_jobs() -> None:
    now = datetime.now(timezone.utc).timestamp()
    for batch_id, batch in eval_batch_jobs.items():
        updated_ts = _timestamp_seconds(batch.get("updated_at"))
        if (
            batch.get("status") not in TERMINAL_STATUSES
            and not _task_is_unfinished(eval_batch_tasks, batch_id)
            and updated_ts is not None
            and now - updated_ts > ACTIVE_WORKFLOW_STALE_SECONDS
        ):
            _terminalize_lost_batch(
                batch,
                reason="Eval batch heartbeat was lost before completion.",
                refresh=_refresh_eval_batch_progress,
            )
            _persist_eval_batch(batch_id, batch)
            lease = paid_work_leases.pop(f"eval-batch:{batch_id}", None)
            if lease is not None:
                lease.close()
    expired = [
        batch_id
        for batch_id, batch in eval_batch_jobs.items()
        if batch.get("status") in TERMINAL_STATUSES
        and not _task_is_unfinished(eval_batch_tasks, batch_id)
        and _is_old(batch, now)
    ]
    for bid in expired:
        eval_batch_jobs.pop(bid, None)
        workflow_store.delete(EVAL_BATCH_RECORD_KIND, bid)
        task = eval_batch_tasks.pop(bid, None)
        if task and not task.done():
            task.cancel()

    if len(eval_batch_jobs) <= WORKFLOW_MAX_ENTRIES:
        return

    candidates = sorted(
        eval_batch_jobs.items(),
        key=lambda item: item[1].get("updated_at", ""),
    )
    for batch_id, batch in candidates:
        if (
            batch.get("status") in TERMINAL_STATUSES
            and not _task_is_unfinished(eval_batch_tasks, batch_id)
        ):
            eval_batch_jobs.pop(batch_id, None)
            workflow_store.delete(EVAL_BATCH_RECORD_KIND, batch_id)
            task = eval_batch_tasks.pop(batch_id, None)
            if task and not task.done():
                task.cancel()
        if len(eval_batch_jobs) <= WORKFLOW_MAX_ENTRIES:
            break


def _touch_eval_batch(batch: dict[str, Any]) -> None:
    batch["updated_at"] = _workflow_now()
    _persist_eval_batch(str(batch.get("id") or ""), batch)


def _get_eval_batch_record(batch_id: str) -> dict[str, Any] | None:
    batch = eval_batch_jobs.get(batch_id)
    if batch is not None:
        batch.setdefault("id", batch_id)
        return batch
    batch = workflow_store.load(EVAL_BATCH_RECORD_KIND, batch_id)
    if batch is not None:
        batch["id"] = batch_id
        eval_batch_jobs[batch_id] = batch
    return batch


def _refresh_eval_batch_progress(batch: dict[str, Any]) -> None:
    entries = batch.get("entries", [])
    batch["completedFiles"] = sum(1 for e in entries if e.get("status") == "completed")
    batch["failedFiles"] = sum(1 for e in entries if e.get("status") == "errored")
    batch["cancelledFiles"] = sum(1 for e in entries if e.get("status") == "cancelled")
    batch["blockedFiles"] = sum(1 for e in entries if e.get("status") == "blocked")
    batch["doneFiles"] = (
        batch["completedFiles"]
        + batch["failedFiles"]
        + batch["cancelledFiles"]
        + batch["blockedFiles"]
    )


def _summarize_eval_batch_entries(entries: list[dict]) -> list[dict]:
    return [
        {
            "name": entry.get("name", ""),
            "folder": entry.get("folder", ""),
            "status": entry.get("status", "unknown"),
            "score": entry.get("score"),
            "itemsEvaluated": entry.get("itemsEvaluated", 0),
            "outputFolder": entry.get("outputFolder", ""),
            "error": entry.get("error", ""),
        }
        for entry in entries
    ]


def _apply_evaluation_report_status(entry: dict[str, Any], report: dict[str, Any]) -> None:
    """Map an evaluation report's outcome to the batch entry terminal state."""
    report_status = str(report.get("status") or "").strip().lower()
    report_error = str(report.get("error") or report.get("reason") or "").strip()

    if report_status in {"complete", "completed"}:
        entry["status"] = "completed"
        entry["error"] = ""
        return
    if report_status in {"cancelled", "canceled"}:
        entry["status"] = "cancelled"
        entry["error"] = report_error or "Evaluation was cancelled"
        return

    entry["status"] = "errored"
    entry["error"] = report_error or (
        f"Evaluation ended with status: {report_status or 'unknown'}"
    )


def _run_summary(run_dir: Path, label: str | None = None) -> dict[str, Any] | None:
    """Summarize a run folder that contains a final_result.json.

    Returns None when the result file is missing or unreadable. Shared by
    ``_list_output_runs`` (eval-batch modal) and the CLI's ``list runs``.
    """
    result_path = run_dir / "final_result.json"
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    generated_questions = data.get("generated_questions")
    question_count = len(generated_questions) if isinstance(generated_questions, list) else 0
    eval_report_path = run_dir / "evaluation_outputs" / "evaluation_report.json"
    existing_score = None
    has_eval = eval_report_path.exists()
    if has_eval:
        try:
            report = json.loads(eval_report_path.read_text(encoding="utf-8"))
            if isinstance(report, dict):
                existing_score = report.get("overall_score")
        except Exception:
            pass
    try:
        modified_at = run_dir.stat().st_mtime
    except OSError:
        return None
    return {
        "folder": label or run_dir.name,
        "label": label or run_dir.name,
        "mtime": modified_at,
        "questionCount": question_count,
        "hasEval": has_eval,
        "existingScore": existing_score,
    }


def _list_output_runs(limit: int = 60) -> list[dict[str, Any]]:
    """List safe dated or legacy output-run labels, newest first."""
    if limit <= 0:
        return []
    try:
        candidates = _iter_artifact_run_dirs(OUTPUTS_DIR)
    except OSError:
        return []
    sortable: list[tuple[float, Path, str]] = []
    selectable: list[tuple[Path, str]] = []
    for run_dir, label in candidates:
        if (run_dir / "final_result.json").is_file():
            selectable.append((run_dir, label))
            continue
        # Batch roots hold one final_result.json per immediate child. Surface
        # those child runs using their full safe relative labels.
        try:
            for child in run_dir.iterdir():
                if (
                    child.is_symlink()
                    or not child.is_dir()
                    or not (child / "final_result.json").is_file()
                ):
                    continue
                child.resolve().relative_to(OUTPUTS_DIR.resolve())
                selectable.append((child, f"{label}/{child.name}"))
        except (OSError, ValueError):
            continue

    for run_dir, label in selectable:
        try:
            sortable.append((run_dir.stat().st_mtime, run_dir, label))
        except OSError:
            continue
    sortable.sort(key=lambda item: item[0], reverse=True)

    runs: list[dict[str, Any]] = []
    for _mtime, run_dir, label in sortable:
        summary = _run_summary(run_dir, label)
        if summary is None:
            continue
        runs.append(summary)
        if len(runs) >= limit:
            break
    return runs


def _eval_batch_tracing_disabled() -> bool:
    """Use the workflow engine's privacy-first SDK tracing policy."""
    from backend.src.workflow.engine import _tracing_disabled  # noqa: PLC0415

    return _tracing_disabled()


async def run_eval_batch_async(batch_id: str) -> None:
    """Run a batch-eval job (prior runs or uploaded PDFs)."""
    import json as _json

    from agents import RunConfig, trace

    from backend.src.eval_pipeline import (
        EvalPdfExtractionRuntime,
        EvalPdfGuardrailBlockedError,
        build_eval_final_result,
        extract_questions_from_pdf,
    )
    from backend.src.evaluation import (
        EVALUATION_OUTPUTS_DIRNAME,
        EVALUATION_REPORT_FILENAME,
        run_openai_output_evaluation,
    )

    batch = eval_batch_jobs[batch_id]
    mode = batch.get("mode", "prior_runs")
    evaluation_budget_lock = asyncio.Lock()
    extraction_runtime: EvalPdfExtractionRuntime | None = None

    try:
        entries = batch.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Eval batch requires at least one entry")
        require_within_limit(
            "Eval batch entry",
            len(entries),
            max_eval_batch_entries(),
            "MAX_EVAL_BATCH_ENTRIES",
        )
        evaluation_item_budget = batch.get("evaluationItemBudget")
        if (
            isinstance(evaluation_item_budget, bool)
            or not isinstance(evaluation_item_budget, int)
            or not 1 <= evaluation_item_budget <= max_eval_batch_items()
        ):
            raise ValueError(
                "Eval batch evaluationItemBudget must be an integer from 1 to "
                f"{max_eval_batch_items()} (MAX_EVAL_BATCH_ITEMS)"
            )

        client = get_openai_client()
        model_provider = configured_agents_sdk_model_provider()
        tracing_disabled = _eval_batch_tracing_disabled()
        batch_output_dir, batch_output_label = create_unique_run_dir(OUTPUTS_DIR, batch_id, suffix="eval-batch")
        batch["status"] = "running"
        batch["outputFolder"] = batch_output_label
        _touch_eval_batch(batch)

        run_config_kwargs: dict[str, Any] = {
            "tracing_disabled": tracing_disabled,
            "trace_include_sensitive_data": False,
            "trace_metadata": {
                "__trace_source__": "batch-eval",
                "batch_id": batch_id,
            },
        }
        if model_provider is not None:
            run_config_kwargs["model_provider"] = model_provider
        run_config = RunConfig(**run_config_kwargs)
        if mode == "upload":
            extraction_runtime = EvalPdfExtractionRuntime(
                client=client,
                run_config=run_config,
                output_dir=batch_output_dir,
                to_thread=_to_thread_limited,
                max_files=len(entries) + 1,
            )

        source_extraction = None
        source_blocked_reason = ""
        if mode == "upload":
            source_pdf_path = Path(batch.get("source_pdf_path", ""))
            if not source_pdf_path.exists():
                raise FileNotFoundError(f"Source PDF not found: {source_pdf_path}")

            try:
                with trace("Batch Eval Upload", disabled=tracing_disabled):
                    source_extraction = await extract_questions_from_pdf(
                        client=client,
                        pdf_path=source_pdf_path,
                        run_config=run_config,
                        generated_pdf=False,
                        to_thread=_to_thread_limited,
                        runtime=extraction_runtime,
                    )
            except EvalPdfGuardrailBlockedError as exc:
                source_blocked_reason = str(exc)
                for entry in batch.get("entries", []):
                    entry["status"] = "blocked"
                    entry["error"] = source_blocked_reason
                _refresh_eval_batch_progress(batch)
                _touch_eval_batch(batch)
            else:
                write_json(
                    batch_output_dir / "source_extraction.json",
                    source_extraction.model_dump(),
                )

        async def _eval_one(entry: dict) -> None:
            async with batch_entry_semaphore:
                if batch.get("cancel_requested") or batch.get("status") == "cancelled":
                    entry["status"] = "cancelled"
                    entry["error"] = "Stopped by user"
                    _refresh_eval_batch_progress(batch)
                    _touch_eval_batch(batch)
                    return

                entry["status"] = "running"
                _refresh_eval_batch_progress(batch)
                _touch_eval_batch(batch)

                child_label = str(entry.get("folder") or entry.get("name", "item"))
                child_stem = (
                    f"{safe_stem(child_label, fallback='item')}-"
                    f"{stable_short_hash(child_label)}"
                )
                child_dir = batch_output_dir / child_stem
                child_dir.mkdir(parents=True, exist_ok=True)

                try:
                    if mode == "prior_runs":
                        source_folder = _resolve_output_folder_path(
                            str(entry.get("folder") or "")
                        )
                        result_path = source_folder / "final_result.json"
                        if not result_path.exists():
                            raise FileNotFoundError(f"final_result.json not found in {entry['folder']}")
                        final_result = _json.loads(result_path.read_text(encoding="utf-8"))
                    else:
                        if source_extraction is None:
                            raise RuntimeError(
                                "Source PDF extraction did not produce a result"
                            )
                        gen_pdf = Path(entry["pdf_path"])
                        generated_extraction = await extract_questions_from_pdf(
                            client=client,
                            pdf_path=gen_pdf,
                            run_config=run_config,
                            generated_pdf=True,
                            to_thread=_to_thread_limited,
                            runtime=extraction_runtime,
                        )
                        write_json(
                            child_dir / "generated_extraction.json",
                            generated_extraction.model_dump(),
                        )
                        final_result = build_eval_final_result(
                            source_extraction=source_extraction,
                            generated_extraction=generated_extraction,
                            generated_pdf=gen_pdf,
                        )

                    generated_questions = final_result.get("generated_questions")
                    if not isinstance(generated_questions, list) or not generated_questions:
                        raise ValueError("Evaluation input has no generated questions")
                    item_count = len(generated_questions)
                    async with evaluation_budget_lock:
                        reserved = int(batch.get("reservedEvaluationItems", 0))
                        if reserved + item_count > evaluation_item_budget:
                            raise ValueError(
                                "Eval-batch item budget exceeded before grader submission: "
                                f"{reserved + item_count} requested, "
                                f"{evaluation_item_budget} allowed"
                            )
                        batch["reservedEvaluationItems"] = reserved + item_count
                        _touch_eval_batch(batch)

                    write_json(child_dir / "final_result.json", final_result)

                    report = await await_cancellation_safe_blocking_call(
                        _to_thread_limited,
                        run_openai_output_evaluation,
                        client=client,
                        final_result=final_result,
                        outputs_dir=child_dir,
                        source_files=[],
                        should_cancel=lambda: bool(
                            batch.get("cancel_requested")
                            or batch.get("status") == "cancelled"
                        ),
                    )
                    # Recompute score excluding latex criterion (no tex file in batch eval)
                    criterion_scores = report.get("criterion_scores") or {}
                    numeric = [
                        value.get("score")
                        for value in criterion_scores.values()
                        if isinstance(value, dict)
                        and isinstance(value.get("score"), (int, float))
                        and value.get("criterion_name") != "latex_pdf_generated"
                    ]
                    overall = round(sum(float(s) for s in numeric) / len(numeric), 3) if numeric else None
                    report["overall_score"] = overall
                    report_path = child_dir / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_REPORT_FILENAME
                    write_json(report_path, report)

                    _apply_evaluation_report_status(entry, report)
                    entry["score"] = overall if entry["status"] == "completed" else None
                    entry["itemsEvaluated"] = report.get("items_evaluated", 0)
                    entry["outputFolder"] = f"{batch_output_label}/{child_stem}"
                except EvalPdfGuardrailBlockedError as exc:
                    decision = exc.decision
                    write_json(
                        child_dir / "blocked.json",
                        {
                            "status": "blocked",
                            "category": decision.category,
                            "reason": str(exc),
                            "stage": decision.stage,
                        },
                    )
                    entry["status"] = "blocked"
                    entry["error"] = str(exc)
                except Exception as exc:
                    error_type = safe_exception_category(exc)
                    error_message = safe_exception_message(
                        exc,
                        operation="Eval batch entry",
                    )
                    write_json(
                        child_dir / "error.json",
                        {
                            "status": "failed",
                            "error": error_message,
                            "error_type": error_type,
                        },
                    )
                    entry["status"] = "errored"
                    entry["error"] = error_message
                finally:
                    _refresh_eval_batch_progress(batch)
                    _touch_eval_batch(batch)

        if not source_blocked_reason:
            await asyncio.gather(*(_eval_one(e) for e in entries))

        _refresh_eval_batch_progress(batch)
        unfinished_files = max(
            0,
            len(batch.get("entries", [])) - int(batch.get("doneFiles", 0)),
        )
        issues: list[str] = []
        if batch.get("failedFiles", 0):
            issues.append(f"{batch['failedFiles']} evaluation(s) failed or were incomplete")
        if batch.get("cancelledFiles", 0):
            issues.append(f"{batch['cancelledFiles']} evaluation(s) were cancelled")
        if batch.get("blockedFiles", 0):
            issues.append(f"{batch['blockedFiles']} evaluation(s) were blocked")
        if unfinished_files:
            issues.append(f"{unfinished_files} evaluation(s) did not reach a terminal state")

        if batch.get("failedFiles", 0) or unfinished_files:
            batch["status"] = "errored"
        elif batch.get("cancel_requested") or batch.get("cancelledFiles", 0):
            batch["status"] = "cancelled"
        elif batch.get("blockedFiles", 0):
            batch["status"] = "blocked"
        else:
            batch["status"] = "completed"
        batch["error"] = "; ".join(issues)

        write_json(
            batch_output_dir / "batch_summary.json",
            {
                "generated_at": datetime.now().isoformat(),
                "batch_id": batch_id,
                "mode": mode,
                "output_folder": batch_output_label,
                "total_files": batch.get("totalFiles", 0),
                "completed_files": batch.get("completedFiles", 0),
                "failed_files": batch.get("failedFiles", 0),
                "cancelled_files": batch.get("cancelledFiles", 0),
                "blocked_files": batch.get("blockedFiles", 0),
                "status": batch.get("status", "unknown"),
                "error": batch.get("error", ""),
                "entries": _summarize_eval_batch_entries(batch.get("entries", [])),
            },
        )
        _touch_eval_batch(batch)
    except asyncio.CancelledError:
        if batch.get("status") != "cancelled":
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
        error_message = safe_exception_message(exc, operation="Eval batch")
        logger.error(
            "Eval batch %s failed (%s)",
            batch_id,
            safe_exception_category(exc),
        )
        batch["status"] = "errored"
        batch["error"] = error_message
        _touch_eval_batch(batch)
    finally:
        if extraction_runtime is not None:
            await extraction_runtime.aclose()


async def pruning_loop() -> None:
    """Periodically prune expired workflows/batches off the request path.

    Started from the FastAPI lifespan. Keeping pruning here (instead of calling it
    on every status poll) means the 150 ms client polling loop no longer triggers
    full registry scans and filesystem cleanup on each request.
    """
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            # Registries and asyncio Tasks belong to this event-loop thread.
            _prune_workflows()
            _prune_batch_jobs()
            _prune_eval_batch_jobs()
            await asyncio.to_thread(
                workflow_store.prune_idempotency,
                older_than=time.time() - IDEMPOTENCY_RETENTION_SECONDS,
            )
            active_outputs = _active_output_labels()
            active_uploads = _active_upload_labels()
            await await_cancellation_safe_blocking_call(
                asyncio.to_thread,
                _cleanup_artifact_directories,
                active_outputs,
                active_uploads,
            )
        except Exception as exc:
            logger.error(
                "Background pruning cycle failed (%s)",
                safe_exception_category(exc),
            )

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
    root = base.resolve()
    for child in base.iterdir():
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            child.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if _DATED_DIR_RE.match(child.name):
            try:
                children = child.iterdir()
                for run in children:
                    try:
                        if run.is_symlink() or not run.is_dir():
                            continue
                        run.resolve().relative_to(root)
                    except (OSError, ValueError):
                        continue
                    runs.append((run, f"{child.name}/{run.name}"))
            except OSError:
                continue
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
    """Return run-dir labels owned by nonterminal records or unfinished tasks."""
    labels: set[str] = set()
    record_groups = (
        (
            list(workflows.items()) + workflow_store.list_records("workflow"),
            workflow_tasks,
        ),
        (
            list(batch_jobs.items()) + workflow_store.list_records("batch"),
            batch_tasks,
        ),
        (
            list(eval_batch_jobs.items())
            + workflow_store.list_records(EVAL_BATCH_RECORD_KIND),
            eval_batch_tasks,
        ),
    )
    for records, tasks in record_groups:
        for record_id, record in records:
            if (
                record.get("status") in TERMINAL_STATUSES
                and not _task_is_unfinished(tasks, record_id)
            ):
                continue
            label = record.get("outputFolder")
            if isinstance(label, str) and label:
                labels.add(_owning_run_label(label))
            resumed_from = record.get("resumedFrom")
            dependency = (
                resumed_from.get("outputFolder")
                if isinstance(resumed_from, dict)
                else None
            )
            if isinstance(dependency, str) and dependency:
                labels.add(_owning_run_label(dependency))
            if record.get("mode") == "prior_runs":
                for entry in record.get("entries", []):
                    dependency = entry.get("folder") if isinstance(entry, dict) else None
                    if isinstance(dependency, str) and dependency:
                        labels.add(_owning_run_label(dependency))
    return labels


def _active_upload_labels() -> set[str]:
    """Return upload labels needed by nonterminal records or unfinished tasks."""
    labels: set[str] = set()
    record_groups = (
        (
            list(workflows.items()) + workflow_store.list_records("workflow"),
            workflow_tasks,
        ),
        (
            list(eval_batch_jobs.items())
            + workflow_store.list_records(EVAL_BATCH_RECORD_KIND),
            eval_batch_tasks,
        ),
    )
    for records, tasks in record_groups:
        for record_id, record in records:
            if (
                record.get("status") in TERMINAL_STATUSES
                and not _task_is_unfinished(tasks, record_id)
            ):
                continue
            label = record.get("uploadFolder")
            if isinstance(label, str) and label:
                labels.add(_owning_run_label(label))
    return labels


def _output_owner_is_terminal(label: str) -> bool | None:
    """Return terminal/quiescent status for an owned output run, if known."""
    owning_label = _owning_run_label(label)
    found = False
    record_groups = (
        (
            workflows,
            workflow_store.list_records("workflow"),
            workflow_tasks,
        ),
        (
            batch_jobs,
            workflow_store.list_records("batch"),
            batch_tasks,
        ),
        (
            eval_batch_jobs,
            workflow_store.list_records(EVAL_BATCH_RECORD_KIND),
            eval_batch_tasks,
        ),
    )
    for live_records, stored_records, tasks in record_groups:
        combined = dict(stored_records)
        combined.update(live_records)
        for record_id, record in combined.items():
            output_label = record.get("outputFolder")
            if (
                not isinstance(output_label, str)
                or not output_label
                or _owning_run_label(output_label) != owning_label
            ):
                continue
            found = True
            if (
                record.get("status") not in TERMINAL_STATUSES
                or _task_is_unfinished(tasks, record_id)
            ):
                return False
    return True if found else None


def _cleanup_artifact_directories(
    active_outputs: set[str],
    active_uploads: set[str] | None = None,
) -> None:
    """Prune artifacts only while no workflow, reader, or writer holds a lease."""
    lease = try_acquire_artifact_mutation_lease()
    if lease is None:
        return
    try:
        _cleanup_artifact_directories_under_lease(
            active_outputs,
            active_uploads,
        )
    finally:
        lease.close()


def _cleanup_artifact_directories_under_lease(
    active_outputs: set[str],
    active_uploads: set[str] | None = None,
) -> None:
    """Prune old upload/output folders without touching active workflows."""
    global _last_artifact_cleanup_ts
    active_uploads = active_uploads or set()
    now = time.time()
    if now - _last_artifact_cleanup_ts < ARTIFACT_CLEANUP_INTERVAL_SECONDS:
        return
    _last_artifact_cleanup_ts = now

    # Prune at the leaf run-dir level (the dated layout nests runs one level
    # below OUTPUTS_DIR/UPLOADS_DIR), then sweep away emptied date folders.
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

    # Serialize pruning with request admission so a newly reserved session can
    # never be deleted between its capacity check and first write.
    with upload_disk_budget.cleanup_guard(UPLOADS_DIR) as reserved_uploads:
        protected_uploads = active_uploads | reserved_uploads
        upload_runs = _iter_artifact_run_dirs(UPLOADS_DIR)
        upload_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
        for path, label in upload_runs:
            if label in protected_uploads:
                continue
            try:
                if now - path.stat().st_mtime > UPLOAD_RETENTION_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
        upload_runs = [
            item
            for item in _iter_artifact_run_dirs(UPLOADS_DIR)
            if item[1] not in protected_uploads
        ]
        upload_runs.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0)
        while (
            sum(_directory_size(item[0]) for item in upload_runs if item[0].exists())
            > UPLOAD_MAX_BYTES
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
    workflow["cancel_requested"] = True
    workflow["status"] = "cancelled"
    workflow["error"] = reason
    now = _workflow_now()
    now_seconds = datetime.now(timezone.utc).timestamp()
    for agent in workflow.get("agents", {}).values():
        if agent.get("status") == "thinking":
            agent["status"] = "cancelled"
            data = agent.setdefault("data", {})
            data.setdefault("endTime", now)
            if data.get("startTime") and not data.get("executionTime"):
                start_seconds = _timestamp_seconds(data["startTime"])
                if start_seconds is not None:
                    data["executionTime"] = max(0.0, now_seconds - start_seconds)
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
    now = _workflow_now()
    now_seconds = datetime.now(timezone.utc).timestamp()
    if error_stage and error_stage in workflow.get("agents", {}):
        agent = workflow["agents"][error_stage]
        agent["status"] = "errored"
        data = agent.setdefault("data", {})
        data["error"] = error
        data.setdefault("endTime", now)
        if data.get("startTime") and not data.get("executionTime"):
            start_seconds = _timestamp_seconds(data["startTime"])
            if start_seconds is not None:
                data["executionTime"] = max(0.0, now_seconds - start_seconds)
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
    status_data_fields = {
        "startTime",
        "endTime",
        "executionTime",
        "receivedFrom",
        "sentTo",
        "model",
        "reasoningLevel",
        "reasoning_level",
        "reasoning",
        "error",
    }
    agents: dict[str, dict[str, Any]] = {}
    for agent_key, agent in workflow.get("agents", {}).items():
        if not isinstance(agent, dict):
            continue
        source_data = agent.get("data")
        compact_data = (
            {
                key: value
                for key, value in source_data.items()
                if key in status_data_fields
            }
            if isinstance(source_data, dict)
            else {}
        )
        agents[str(agent_key)] = {
            "status": agent.get("status", "inactive"),
            "data": compact_data,
        }
    return {
        "workflowId": workflow_id,
        "status": workflow.get("status", "unknown"),
        "agents": agents,
        "outputFolder": workflow.get("outputFolder", ""),
        "usage": workflow.get("usage", {}),
        "error": workflow.get("error", ""),
        "errorStage": workflow.get("errorStage"),
        "blocked": workflow.get("blocked"),
    }


def reconcile_persisted_state() -> None:
    """Load durable records and terminalize work orphaned by a process restart."""
    for lease in paid_work_leases.values():
        lease.close()
    paid_work_leases.clear()
    workflow_admission_reservations.clear()
    workflow_admission_owners.clear()
    # The deployment runs one backend process. Lifespan startup is therefore
    # the exclusive point where claims interrupted before resource persistence
    # can be released without racing a live request owner.
    workflow_store.reconcile_idempotency()
    now = _workflow_now()
    for workflow_id, workflow in workflow_store.list_records("workflow"):
        workflow["id"] = workflow_id
        if workflow.get("status") not in TERMINAL_STATUSES:
            _terminalize_lost_workflow(
                workflow,
                "Server restarted before this workflow completed.",
            )
            workflow["updated_at"] = now
            _persist_workflow(workflow_id, workflow)
        workflows[workflow_id] = workflow

    for kind, registry, persist, refresh in (
        ("batch", batch_jobs, _persist_batch, _refresh_batch_progress),
        (
            EVAL_BATCH_RECORD_KIND,
            eval_batch_jobs,
            _persist_eval_batch,
            _refresh_eval_batch_progress,
        ),
    ):
        for batch_id, batch in workflow_store.list_records(kind):
            batch["id"] = batch_id
            if batch.get("status") not in TERMINAL_STATUSES:
                batch["status"] = "errored"
                batch["error"] = "Server restarted before this batch completed."
                batch["updated_at"] = now
                for entry in batch.get("entries", []):
                    if (
                        isinstance(entry, dict)
                        and entry.get("status") not in TERMINAL_STATUSES
                    ):
                        entry["status"] = "errored"
                        entry["error"] = batch["error"]
                refresh(batch)
                persist(batch_id, batch)
            registry[batch_id] = batch

    _prune_workflows()
    _prune_batch_jobs()
    _prune_eval_batch_jobs()


async def shutdown_runtime_tasks() -> None:
    """Cancel tracked jobs, persist terminal state, and close the provider client."""
    active_tasks = [
        task
        for registry in (workflow_tasks, batch_tasks, eval_batch_tasks)
        for task in registry.values()
        if not task.done()
    ]
    # Signal cancellation before any synchronous SQLite persistence. Otherwise
    # a busy database can leave paid provider work running for its full timeout.
    for task in active_tasks:
        task.cancel()

    for workflow_id, task in list(workflow_tasks.items()):
        if task.done():
            continue
        workflow = workflows.get(workflow_id)
        if workflow is not None and workflow.get("status") not in TERMINAL_STATUSES:
            try:
                _mark_workflow_cancelled(
                    workflow,
                    reason="Server is shutting down.",
                )
            except Exception as exc:
                logger.error(
                    "Could not persist workflow %s shutdown state (%s)",
                    workflow_id,
                    safe_exception_category(exc),
                )

    for registry, tasks, refresh, touch in (
        (batch_jobs, batch_tasks, _refresh_batch_progress, _touch_batch_job),
        (
            eval_batch_jobs,
            eval_batch_tasks,
            _refresh_eval_batch_progress,
            _touch_eval_batch,
        ),
    ):
        for batch_id, task in list(tasks.items()):
            if task.done():
                continue
            batch = registry.get(batch_id)
            if batch is None or batch.get("status") in TERMINAL_STATUSES:
                continue
            batch["status"] = "cancelled"
            batch["error"] = "Server is shutting down."
            batch["cancel_requested"] = True
            for entry in batch.get("entries", []):
                if (
                    isinstance(entry, dict)
                    and entry.get("status") not in TERMINAL_STATUSES
                ):
                    entry["status"] = "cancelled"
                    entry["error"] = batch["error"]
            refresh(batch)
            try:
                touch(batch)
            except Exception as exc:
                logger.error(
                    "Could not persist batch %s shutdown state (%s)",
                    batch_id,
                    safe_exception_category(exc),
                )

    if active_tasks:
        await asyncio.gather(*active_tasks, return_exceptions=True)
    workflow_tasks.clear()
    batch_tasks.clear()
    eval_batch_tasks.clear()
    async with workflow_admission_lock:
        workflow_admission_reservations.clear()
        workflow_admission_owners.clear()
        for lease in paid_work_leases.values():
            lease.close()
        paid_work_leases.clear()

    global _openai_client
    client, _openai_client = _openai_client, None
    close = getattr(client, "close", None)
    if callable(close):
        try:
            await asyncio.wait_for(asyncio.to_thread(close), timeout=5)
        except Exception as exc:
            logger.warning(
                "Could not close the OpenAI client during shutdown (%s)",
                safe_exception_category(exc),
            )
    try:
        await asyncio.wait_for(close_agents_sdk_model_provider(), timeout=5)
    except Exception as exc:
        logger.warning(
            "Could not close the Agents SDK client during shutdown (%s)",
            safe_exception_category(exc),
        )


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
        runnable_sequence = INPUT_TO_AGENT_SEQUENCE
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


async def _read_upload_limited(
    upload: Any,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    label: str = "Upload",
) -> bytes:
    """Read an async upload in chunks while enforcing a hard byte ceiling."""
    content = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} exceeds {max_bytes} bytes",
            )
    return bytes(content)


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

    original_filename = Path(filename or "source.txt").name
    original_suffix = Path(original_filename).suffix.lower()
    if original_suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Normalizer and Extracter starts need a source file with one of: "
                + ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            ),
        )

    stored_filename = safe_filename(
        original_filename,
        fallback="source.txt",
        allowed_suffixes=ALLOWED_UPLOAD_EXTENSIONS,
    )
    suffix = Path(stored_filename).suffix.lower()
    if suffix in UPLOAD_MAGIC_SIGNATURES and not upload_magic_matches(
        suffix,
        content[:1024],
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Source file does not appear to be a valid {suffix} file",
        )

    session_dir = UPLOADS_DIR / f"input_to_agent_{workflow_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    file_path = session_dir / stored_filename
    file_path.write_bytes(content)
    return file_path


def _discard_input_to_agent_upload(workflow_id: str) -> None:
    """Remove a failed input-to-agent upload session."""
    shutil.rmtree(UPLOADS_DIR / f"input_to_agent_{workflow_id}", ignore_errors=True)


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
    if not _is_under_directory(session_dir, UPLOADS_DIR) or not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Upload session not found")

    manifest = _load_upload_manifest(session_dir)
    requested = [
        str(file_id).strip()
        for file_id in (selected_file_ids or [])
        if str(file_id).strip()
    ]
    if not requested:
        raise HTTPException(
            status_code=400,
            detail="fileIds must contain at least one uploaded file when sessionId is provided",
        )

    files_by_id: dict[str, dict[str, Any]] = {}
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("fileId") or "")
        if file_id:
            files_by_id[file_id] = item

    missing_ids = [file_id for file_id in requested if file_id not in files_by_id]
    if missing_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown fileIds: {', '.join(dict.fromkeys(missing_ids))}",
        )

    paths: list[str] = []
    seen_ids: set[str] = set()
    for file_id in requested:
        if file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        item = files_by_id[file_id]
        stored_name = str(item.get("storedName") or "")
        try:
            path = ensure_child_path(session_dir, stored_name)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Upload manifest contains an invalid path for fileId {file_id}",
            ) from exc
        if not path.is_file() or path.suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file is missing or unsupported for fileId {file_id}",
            )
        paths.append(str(path))
    return paths

async def _to_thread_limited(func, /, *args, **kwargs):
    """Run blocking SDK/filesystem work in a bounded thread pool."""
    async with blocking_openai_semaphore:
        return await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            func,
            *args,
            **kwargs,
        )

def _raise_schema_match_invalid(start_agent: str, exc: Exception | None = None) -> None:
    """Raise the standard agent-input schema validation error."""
    if exc is not None:
        logger.warning(
            "Schema match invalid for %s input (%s)",
            start_agent,
            safe_exception_category(exc),
        )
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
    blocked = sum(1 for entry in entries if entry.get("status") == "blocked")
    batch["completedFiles"] = completed
    batch["failedFiles"] = failed
    batch["cancelledFiles"] = cancelled
    batch["blockedFiles"] = blocked
    batch["doneFiles"] = completed + failed + cancelled + blocked

async def run_workflow_async(
    workflow_id: str,
    prompt: str,
    file_paths: list[str],
    *,
    run_evaluation: bool = False,
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
    run_evaluation: bool = False,
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
        run_evaluation=run_evaluation,
    )

async def run_workflow_from_agent_input_async(
    workflow_id: str,
    prompt: str,
    *,
    file_paths: list[str],
    start_agent: str,
    agent_input_payload: Any = None,
    input_filename: str,
    run_evaluation: bool = False,
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
        run_evaluation=run_evaluation,
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
    run_evaluation: bool = False,
    theme: str = "",
) -> None:
    """Run the workflow via the shared engine; surface FastAPI cancel/error states."""
    client = get_openai_client()
    token_queue: asyncio.Queue | None = None
    emit_token = None
    if WORKFLOW_TOKEN_STREAMING_ENABLED:
        token_queue = asyncio.Queue(maxsize=2000)
        workflow_token_queues[workflow_id] = token_queue
        emit_token = _make_emit_token(token_queue)
    try:
        async with workflow_execution_semaphore:
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
                emit_token=emit_token,
                model_provider=configured_agents_sdk_model_provider(),
            )
    except asyncio.CancelledError:
        logger.info("Workflow %s cancelled", workflow_id)
        if workflow.get("status") != "cancelled":
            _mark_workflow_cancelled(workflow)
        raise
    except Exception as exc:
        error_message = safe_exception_message(exc, operation="Workflow")
        logger.error(
            "Workflow %s failed (%s)",
            workflow_id,
            safe_exception_category(exc),
        )
        _mark_workflow_error(workflow, error_message)
        raise
    finally:
        # Drop the registry reference; an attached SSE stream keeps its own local
        # reference to drain the tail, then ends on the terminal status.
        if token_queue is not None:
            workflow_token_queues.pop(workflow_id, None)

async def run_create_data_batch_async(batch_id: str):
    """Run the workflow against every evaluation file in parallel."""
    batch = batch_jobs[batch_id]
    reservation_id = str(
        batch.get("workflowAdmissionReservationId")
        or f"create-data:{batch_id}"
    )
    workflow_slots = max(
        1,
        int(
            batch.get("workflowAdmissionSlots")
            or min(len(batch.get("entries", [])), BATCH_WORKFLOW_CONCURRENCY)
        ),
    )
    reservation_ready = bool(batch.get("workflowAdmissionReservationId"))

    try:
        entries = batch.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Create-data batch requires at least one entry")
        require_within_limit(
            "Create-data batch entry",
            len(entries),
            max_create_data_entries(),
            "MAX_CREATE_DATA_ENTRIES",
        )
        await _ensure_workflow_reservation(reservation_id, workflow_slots)
        reservation_ready = True
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
            async with batch_entry_semaphore:
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
                workflow: dict[str, Any] | None = None
                relative_path = entry.get("relativePath", "")
                source_path = (EVALUATION_DIR / relative_path).resolve()
                child_name = f"{safe_stem(str(entry.get('folderName') or source_path.stem), fallback='item')}-{stable_short_hash(relative_path)}"
                output_folder_label = f"{batch_output_label}/{child_name}"
                child_output_dir = batch_output_dir / child_name

                try:
                    candidate_workflow = _new_workflow_record()
                    candidate_workflow["parentBatchId"] = batch_id
                    await _admit_workflow_record(
                        workflow_id,
                        candidate_workflow,
                        reservation_id=reservation_id,
                    )
                    workflow = candidate_workflow
                    entry["workflowId"] = workflow_id

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
                    entry["outputFolder"] = output_folder_label
                    if workflow.get("status") == "blocked":
                        blocked = workflow.get("blocked") or {}
                        entry["status"] = "blocked"
                        entry["error"] = str(
                            blocked.get("reason")
                            or blocked.get("category")
                            or "Workflow blocked by guardrails"
                        )
                    else:
                        if workflow.get("status") not in TERMINAL_STATUSES:
                            # The real engine terminalizes successful records.
                            # Keep the batch invariant explicit for alternate
                            # runners/tests so the reserved slot can be reused.
                            workflow["status"] = "completed"
                            _touch_workflow(workflow)
                        entry["status"] = "completed"
                        entry["error"] = ""
                except asyncio.CancelledError:
                    if workflow is not None:
                        _mark_workflow_cancelled(workflow)
                    entry["status"] = "cancelled"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = "Stopped by user"
                    raise
                except Exception as exc:
                    error_message = safe_exception_message(
                        exc,
                        operation="Create-data entry",
                    )
                    if workflow is not None:
                        workflow["outputFolder"] = output_folder_label
                        _mark_workflow_error(workflow, error_message)
                    entry["status"] = "errored"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = error_message
                finally:
                    _refresh_batch_progress(batch)
                    _touch_batch_job(batch)

        await asyncio.gather(*(run_entry(entry) for entry in entries))

        _refresh_batch_progress(batch)
        issues: list[str] = []
        if batch.get("failedFiles", 0):
            issues.append(f"{batch['failedFiles']} failed")
        if batch.get("cancelledFiles", 0):
            issues.append(f"{batch['cancelledFiles']} cancelled")
        if batch.get("blockedFiles", 0):
            issues.append(f"{batch['blockedFiles']} blocked")

        if batch.get("failedFiles", 0):
            batch["status"] = "errored"
        elif batch.get("cancel_requested") or batch.get("cancelledFiles", 0):
            batch["status"] = "cancelled"
        elif batch.get("blockedFiles", 0):
            batch["status"] = "blocked"
        else:
            batch["status"] = "completed"
        batch["error"] = (
            f"{', '.join(issues)} of {batch['totalFiles']} file workflows did not complete."
            if issues
            else ""
        )

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
                    "blocked_files": batch.get("blockedFiles", 0),
                    "status": batch.get("status", "unknown"),
                    "error": batch.get("error", ""),
                    "entries": _summarize_batch_entries(batch.get("entries", [])),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        batch["summaryFile"] = summary_path.name
        _touch_batch_job(batch)
        logger.info(
            "Create-data batch %s finished with status %s",
            batch_id,
            batch["status"],
        )
    except asyncio.CancelledError:
        logger.info("Create-data batch %s cancelled", batch_id)
        if batch.get("status") != "cancelled":
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
        error_message = safe_exception_message(
            exc,
            operation="Create-data batch",
        )
        logger.error(
            "Create-data batch %s failed (%s)",
            batch_id,
            safe_exception_category(exc),
        )
        batch["status"] = "errored"
        batch["error"] = error_message
        _touch_batch_job(batch)
    finally:
        if reservation_ready:
            await _release_workflow_reservation(reservation_id)

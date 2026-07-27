"""Upload and download endpoints."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import shutil
from pathlib import Path
from typing import BinaryIO

from agents.logger import logger
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.src.api.admin import require_service_available
from backend.src.api.artifact_access import (
    acquire_artifact_access_lease,
    acquire_artifact_mutation_lease,
    close_artifact_lease,
)
from backend.src.api.state import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    TERMINAL_STATUSES,
    UPLOAD_MAGIC_SIGNATURES,
    UPLOAD_MAX_BYTES,
    _build_workflow_zip,
    _downloadable_file_names,
    _get_batch_record,
    _get_eval_batch_record,
    _get_workflow_record,
    _is_under_directory,
    _prune_workflows,
    _resolve_output_folder_label,
    _workflow_zip_is_current,
    _write_upload_manifest,
    batch_jobs,
    batch_tasks,
    eval_batch_tasks,
    upload_magic_matches,
    workflow_tasks,
)
from backend.src.api.upload_budget import upload_disk_budget
from backend.src.paths import (
    OUTPUTS_DIR,
    UPLOADS_DIR,
)
from backend.src.provider_timeouts import await_cancellation_safe_blocking_call
from backend.src.safety import (
    ensure_child_path,
    safe_filename,
    stable_short_hash,
    unique_child_name,
)

router = APIRouter()

_zip_build_tasks: dict[str, asyncio.Task[None]] = {}
_zip_build_tasks_lock = asyncio.Lock()
_zip_build_semaphore = asyncio.Semaphore(1)


def _get_download_owner(
    record_id: str,
) -> tuple[dict[str, object] | None, asyncio.Task | None]:
    """Resolve an output owner and any task still mutating its artifacts."""
    workflow = _get_workflow_record(record_id)
    if workflow is not None:
        task = workflow_tasks.get(record_id)
        parent_batch_id = str(workflow.get("parentBatchId") or "")
        if task is None and parent_batch_id:
            task = batch_tasks.get(parent_batch_id)
        if task is None and not parent_batch_id:
            # Backward compatibility for child records created before the
            # explicit parentBatchId field was introduced.
            for batch_id, batch in batch_jobs.items():
                if any(
                    entry.get("workflowId") == record_id
                    for entry in batch.get("entries", [])
                    if isinstance(entry, dict)
                ):
                    task = batch_tasks.get(batch_id)
                    break
        return workflow, task

    batch = _get_batch_record(record_id)
    if batch is not None:
        return batch, batch_tasks.get(record_id)

    eval_batch = _get_eval_batch_record(record_id)
    if eval_batch is not None:
        return eval_batch, eval_batch_tasks.get(record_id)

    return None, None


def _copy_upload_to_path(
    source: BinaryIO,
    destination: Path,
    *,
    original_name: str,
    request_total: int,
    reservation_id: str,
) -> tuple[int, int, bytes]:
    """Copy one spooled multipart file without blocking the event loop."""
    source.seek(0)
    total_size = 0
    first_bytes = b""
    with destination.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            if not first_bytes:
                first_bytes = chunk[:1024]
            total_size += len(chunk)
            request_total += len(chunk)
            if total_size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File too large: {original_name}. "
                        f"Max size is {MAX_UPLOAD_BYTES} bytes."
                    ),
                )
            if request_total > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Upload exceeds the total request size limit of "
                        f"{MAX_UPLOAD_TOTAL_BYTES} bytes."
                    ),
                )
            upload_disk_budget.ensure(
                reservation_id,
                request_total,
                capacity_bytes=UPLOAD_MAX_BYTES,
            )
            output.write(chunk)
    return total_size, request_total, first_bytes


async def _build_zip_single_flight(
    workflow_id: str,
    output_dir: Path,
    zip_path: Path,
) -> None:
    """Build one archive per workflow while all concurrent callers await it."""
    async with _zip_build_tasks_lock:
        task = _zip_build_tasks.get(workflow_id)
        if task is None or task.done():
            async def build() -> None:
                async with _zip_build_semaphore:
                    if await asyncio.to_thread(
                        _workflow_zip_is_current,
                        output_dir,
                        zip_path,
                    ):
                        return
                    await asyncio.to_thread(
                        _build_workflow_zip,
                        output_dir,
                        zip_path,
                    )

            task = asyncio.create_task(build())
            _zip_build_tasks[workflow_id] = task
    try:
        await asyncio.shield(task)
    finally:
        if task.done():
            async with _zip_build_tasks_lock:
                if _zip_build_tasks.get(workflow_id) is task:
                    _zip_build_tasks.pop(workflow_id, None)


@router.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files for processing."""
    require_service_available()
    _prune_workflows()
    logger.info("Upload request received with %d file(s)", len(files))
    uploaded_files: list[dict[str, object]] = []
    session_dir: Path | None = None
    session_id = ""
    reservation_id = ""
    reservation_held = False
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Too many files. Maximum is {MAX_UPLOAD_FILES}.",
            )
        projected_bytes = 0
        for file in files:
            if not file.filename:
                raise HTTPException(status_code=400, detail="Every upload needs a filename")
            original_name = Path(file.filename).name
            if Path(original_name).suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type for {original_name}",
                )
            declared_size = getattr(file, "size", None)
            if isinstance(declared_size, int) and declared_size >= 0:
                if declared_size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File too large: {original_name}. "
                            f"Max size is {MAX_UPLOAD_BYTES} bytes."
                        ),
                    )
                projected_bytes += declared_size
                if projected_bytes > MAX_UPLOAD_TOTAL_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Upload exceeds the total request size limit of "
                            f"{MAX_UPLOAD_TOTAL_BYTES} bytes."
                        ),
                    )

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            # 16 random bytes provide 128 bits of session-id entropy.
            session_id = secrets.token_urlsafe(16)
            candidate = UPLOADS_DIR / session_id
            reservation_id = f"upload:{session_id}"
            await upload_disk_budget.reserve_async(
                reservation_id,
                root=UPLOADS_DIR,
                session_dir=candidate,
                requested_bytes=projected_bytes,
                capacity_bytes=UPLOAD_MAX_BYTES,
            )
            reservation_held = True
            try:
                candidate.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                await upload_disk_budget.release_async(reservation_id)
                reservation_held = False
                continue
            session_dir = candidate
            break
        if session_dir is None:
            raise HTTPException(status_code=500, detail="Could not allocate an upload session")

        manifest = {"sessionId": session_id, "files": []}
        used_names: set[str] = set()
        request_total = 0
        logger.info("Created upload session")

        for file_index, file in enumerate(files, start=1):
            # Sanitize filename to prevent path traversal.
            original_name = Path(file.filename).name
            # Reject on the ORIGINAL extension first. safe_filename() would
            # otherwise silently remap a disallowed suffix to the fallback's
            # (.txt), making the rejection below unreachable and storing e.g.
            # an .exe/.zip as .txt instead of refusing it.
            original_extension = Path(original_name).suffix.lower()
            if original_extension not in ALLOWED_UPLOAD_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type for {original_name}",
                )
            stored_name = safe_filename(
                original_name,
                fallback="source.txt",
                allowed_suffixes=ALLOWED_UPLOAD_EXTENSIONS,
            )
            extension = Path(stored_name).suffix.lower()
            stored_name = unique_child_name(session_dir, stored_name, used_names)
            file_path = session_dir / stored_name
            file_id = stable_short_hash(
                f"{session_id}:{stored_name}:{len(used_names)}",
                12,
            )
            logger.info("Saving upload item %d", file_index)

            try:
                total_size, request_total, first_bytes = (
                    await await_cancellation_safe_blocking_call(
                        asyncio.to_thread,
                        _copy_upload_to_path,
                        file.file,
                        file_path,
                        original_name=original_name,
                        request_total=request_total,
                        reservation_id=reservation_id,
                    )
                )
                if (
                    extension in UPLOAD_MAGIC_SIGNATURES
                    and not upload_magic_matches(extension, first_bytes)
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"File {original_name} does not appear to be a valid "
                            f"{extension} file."
                        )
                    )
                item = {
                    "fileId": file_id,
                    "originalName": original_name,
                    "storedName": stored_name,
                    "name": original_name,
                    "size": total_size,
                }
                uploaded_files.append(item)
                manifest["files"].append(item)
                logger.info("Saved upload item %d (%d bytes)", file_index, total_size)
            except Exception as file_error:
                file_path.unlink(missing_ok=True)
                logger.error(
                    "Failed to save upload item %d (%s)",
                    file_index,
                    type(file_error).__name__,
                )
                if isinstance(file_error, HTTPException):
                    raise
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to save file {original_name}",
                ) from file_error
        manifest_size = len(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        )
        upload_disk_budget.ensure(
            reservation_id,
            request_total + manifest_size,
            capacity_bytes=UPLOAD_MAX_BYTES,
        )
        _write_upload_manifest(session_dir, manifest)
    except asyncio.CancelledError:
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise
    except HTTPException:
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise
    except Exception as e:
        if session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        logger.error("Upload failed (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Upload failed") from e
    finally:
        if reservation_held:
            await upload_disk_budget.release_async(reservation_id)
        for file in files:
            try:
                await file.close()
            except Exception as close_error:
                logger.warning(
                    "Failed to close an upload (%s)",
                    type(close_error).__name__,
                )

    return {
        "success": True,
        "sessionId": session_id,
        "files": uploaded_files,
    }

@router.get("/api/download-zip/{workflow_id}")
async def download_workflow_zip(workflow_id: str):
    """Download every file from a workflow or batch output as one zip archive.

    The archive is built once into the workflow's output folder and reused on
    subsequent requests.
    """
    lease = acquire_artifact_mutation_lease()
    try:
        record, owner_task = _get_download_owner(workflow_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Output record not found")
        if record.get("status") not in TERMINAL_STATUSES or (
            owner_task is not None and not owner_task.done()
        ):
            raise HTTPException(
                status_code=409,
                detail="Output archive is not ready until the run has fully stopped.",
            )
        output_folder = record.get("outputFolder")
        if not output_folder:
            raise HTTPException(status_code=404, detail="Output folder not found")

        output_dir = (OUTPUTS_DIR / output_folder).resolve()
        if not _is_under_directory(output_dir, OUTPUTS_DIR) or not output_dir.is_dir():
            raise HTTPException(status_code=404, detail="Output folder not found")

        # output_folder may be a nested label (e.g. 06-29-2026/14-23-05); the zip
        # filename must stay a flat basename so it lands inside the run folder.
        zip_name = f"{Path(output_folder).name}.zip"
        zip_path = output_dir / zip_name
        try:
            await _build_zip_single_flight(workflow_id, output_dir, zip_path)
        except Exception as e:
            logger.error(
                "Failed to build zip for workflow %s (%s)",
                workflow_id,
                type(e).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to build zip archive",
            ) from e
    except BaseException:
        lease.close()
        raise

    return FileResponse(
        path=zip_path,
        filename=zip_name,
        media_type="application/zip",
        content_disposition_type="attachment",
        background=BackgroundTask(close_artifact_lease, lease),
    )


def _leased_file_response(
    *,
    path: Path,
    filename: str,
    media_type: str,
    content_disposition_type: str,
) -> FileResponse:
    lease = acquire_artifact_access_lease()
    try:
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(
            path=path,
            filename=filename,
            media_type=media_type,
            content_disposition_type=content_disposition_type,
            background=BackgroundTask(close_artifact_lease, lease),
        )
    except BaseException:
        lease.close()
        raise


@router.get("/api/output-artifact")
async def download_output_artifact(folder: str, file: str):
    """Download one contained artifact from a known output-folder label."""
    output_dir = _resolve_output_folder_label(folder)
    try:
        artifact_path = ensure_child_path(output_dir, file)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid artifact path") from exc
    if not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media_type, _ = mimetypes.guess_type(str(artifact_path))
    return _leased_file_response(
        path=artifact_path,
        filename=artifact_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="attachment",
    )


@router.get("/api/download/{workflow_id}/{file_path:path}")
async def download_file(workflow_id: str, file_path: str, inline: bool = False):
    """Download a file from a completed workflow."""
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
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

    return _leased_file_response(
        path=resolved_file_path,
        filename=relative_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type=content_disposition_type,
    )

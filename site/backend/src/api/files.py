"""Upload and download endpoints."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from agents.logger import logger
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.src.api.admin import require_service_available
from backend.src.api.state import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    UPLOAD_MAGIC_SIGNATURES,
    _build_workflow_zip,
    _downloadable_file_names,
    _get_workflow_record,
    _is_under_directory,
    _prune_workflows,
    _touch_workflow,
    _write_upload_manifest,
)
from backend.src.paths import (
    OUTPUTS_DIR,
    UPLOADS_DIR,
)
from backend.src.safety import (
    create_unique_run_dir,
    safe_filename,
    stable_short_hash,
    unique_child_name,
)

router = APIRouter()

@router.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files for processing."""
    require_service_available()
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
        # Create a dated session directory for uploads: <mm-dd-yyyy>/<hh-mm-ss>.
        # session_id is the posix-relative label under UPLOADS_DIR.
        session_dir, session_id = create_unique_run_dir(UPLOADS_DIR, "upload")
        manifest = {"sessionId": session_id, "files": []}
        used_names: set[str] = set()
        request_total = 0
        logger.info("Created session directory: %s", session_dir)

        for file in files:
            if file.filename:
                # Sanitize filename to prevent path traversal
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
                file_id = stable_short_hash(f"{session_id}:{stored_name}:{len(used_names)}", 12)
                logger.info("Saving file: %s", stored_name)

                try:
                    total_size = 0
                    first_bytes = b""
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            if not first_bytes:
                                first_bytes = chunk[:1024]
                            total_size += len(chunk)
                            request_total += len(chunk)
                            if total_size > MAX_UPLOAD_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"File too large: {original_name}. Max size is {MAX_UPLOAD_BYTES} bytes.",
                                )
                            if request_total > MAX_UPLOAD_TOTAL_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=(
                                        "Upload exceeds the total request size limit of "
                                        f"{MAX_UPLOAD_TOTAL_BYTES} bytes."
                                    ),
                                )
                            f.write(chunk)
                    signatures = UPLOAD_MAGIC_SIGNATURES.get(extension)
                    if signatures and not any(sig in first_bytes for sig in signatures):
                        raise HTTPException(
                            status_code=400,
                            detail=f"File {original_name} does not appear to be a valid {extension} file.",
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
                    logger.info("Saved file %s (%d bytes)", stored_name, total_size)
                except Exception as file_error:
                    if file_path.exists():
                        file_path.unlink(missing_ok=True)
                    logger.error("Failed to save file %s: %s", stored_name, file_error)
                    if isinstance(file_error, HTTPException):
                        raise
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to save file {original_name}",
                    ) from file_error
                finally:
                    await file.close()
        _write_upload_manifest(session_dir, manifest)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload error: %s", e)
        raise HTTPException(status_code=500, detail="Upload failed") from e


    return {
        "success": True,
        "sessionId": session_id,
        "files": uploaded_files,
    }

@router.get("/api/download-zip/{workflow_id}")
async def download_workflow_zip(workflow_id: str):
    """Download every file from a completed workflow as one zip archive.

    The archive is built once into the workflow's output folder and reused on
    subsequent requests.
    """
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _touch_workflow(workflow)
    output_folder = workflow.get("outputFolder")
    if not output_folder:
        raise HTTPException(status_code=404, detail="Output folder not found")

    output_dir = (OUTPUTS_DIR / output_folder).resolve()
    if not _is_under_directory(output_dir, OUTPUTS_DIR) or not output_dir.is_dir():
        raise HTTPException(status_code=404, detail="Output folder not found")

    # output_folder may be a nested label (e.g. 06-29-2026/14-23-05); the zip
    # filename must stay a flat basename so it lands inside the run folder.
    zip_name = f"{Path(output_folder).name}.zip"
    zip_path = output_dir / zip_name
    if not zip_path.exists():
        try:
            await asyncio.to_thread(_build_workflow_zip, output_dir, zip_path)
        except Exception as e:
            logger.error("Failed to build zip for workflow %s: %s", workflow_id, e)
            raise HTTPException(status_code=500, detail="Failed to build zip archive") from e

    return FileResponse(
        path=zip_path,
        filename=zip_name,
        media_type="application/zip",
        content_disposition_type="attachment",
    )

@router.get("/api/download/{workflow_id}/{file_path:path}")
async def download_file(workflow_id: str, file_path: str, inline: bool = False):
    """Download a file from a completed workflow."""
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

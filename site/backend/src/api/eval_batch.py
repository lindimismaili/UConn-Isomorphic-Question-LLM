"""Batch evaluation endpoints (eval-only runs over prior outputs or uploaded PDFs)."""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from agents.logger import logger
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from backend.src.api import state as api_state
from backend.src.api.artifact_access import artifact_access_endpoint
from backend.src.api.idempotency import (
    IdempotencyDecision,
    abandon_paid_start,
    claim_paid_start,
    commit_paid_start,
    file_fingerprint,
    request_fingerprint,
    validate_idempotency_key,
)
from backend.src.api.state import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    TERMINAL_STATUSES,
    UPLOAD_MAX_BYTES,
    _acquire_paid_work_lease_locked,
    _discard_eval_batch_record,
    _ensure_batch_capacity,
    _get_eval_batch_record,
    _list_output_runs,
    _persist_eval_batch,
    _prune_eval_batch_jobs,
    _refresh_eval_batch_progress,
    _release_paid_work_lease,
    _summarize_eval_batch_entries,
    _touch_eval_batch,
    _workflow_now,
    batch_admission_lock,
    eval_batch_jobs,
    eval_batch_tasks,
    get_openai_client,
    run_eval_batch_async,
    upload_magic_matches,
)
from backend.src.api.upload_budget import upload_disk_budget
from backend.src.cost_controls import (
    grader_max_completion_tokens,
    max_eval_batch_entries,
    max_eval_batch_items,
    max_evaluation_items,
)
from backend.src.evaluation import question_level_criteria
from backend.src.paths import OUTPUTS_DIR, UPLOADS_DIR
from backend.src.provider_timeouts import await_cancellation_safe_blocking_call
from backend.src.safety import (
    safe_exception_category,
    safe_filename,
    unique_child_name,
)

router = APIRouter()

MAX_EVAL_SELECTIONS = max_eval_batch_entries()
MAX_EVAL_BATCH_ITEMS = max_eval_batch_items()
MAX_UPLOAD_EXTRACTION_FILES = MAX_EVAL_SELECTIONS + 1
MAX_UPLOAD_EXTRACTION_CALLS = MAX_UPLOAD_EXTRACTION_FILES * 2
MAX_UPLOAD_BOOTSTRAP_CALLS = MAX_UPLOAD_EXTRACTION_FILES
PDF_SUFFIXES = {".pdf"}


def _projected_pdf_upload_bytes(uploads: list[UploadFile]) -> int:
    """Validate declared upload metadata and return its known byte total."""
    total = 0
    for upload in uploads:
        original_name = Path(upload.filename or "").name
        if not original_name or Path(original_name).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="Eval batch uploads must be PDF files")
        declared_size = getattr(upload, "size", None)
        if isinstance(declared_size, int) and declared_size >= 0:
            if declared_size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"PDF {original_name} exceeds the per-file limit of "
                        f"{MAX_UPLOAD_BYTES} bytes"
                    ),
                )
            total += declared_size
            if total > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Eval batch upload exceeds the aggregate limit of "
                        f"{MAX_UPLOAD_TOTAL_BYTES} bytes"
                    ),
                )
    return total


def _copy_pdf_upload(
    source: BinaryIO,
    destination: Path,
    *,
    original_name: str,
    request_total: int,
    reservation_id: str,
) -> tuple[int, int, bytes]:
    """Copy one spooled eval upload without blocking the event loop."""
    source.seek(0)
    file_total = 0
    first_bytes = b""
    with destination.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            if not first_bytes:
                first_bytes = chunk[:1024]
            file_total += len(chunk)
            request_total += len(chunk)
            if file_total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"PDF {original_name} exceeds the per-file limit of "
                        f"{MAX_UPLOAD_BYTES} bytes"
                    ),
                )
            if request_total > MAX_UPLOAD_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Eval batch upload exceeds the aggregate limit of "
                        f"{MAX_UPLOAD_TOTAL_BYTES} bytes"
                    ),
                )
            upload_disk_budget.ensure(
                reservation_id,
                request_total,
                capacity_bytes=UPLOAD_MAX_BYTES,
            )
            output.write(chunk)
    return file_total, request_total, first_bytes


async def _save_pdf_upload(
    upload: UploadFile,
    *,
    session_dir: Path,
    used_names: set[str],
    request_total: int,
    reservation_id: str,
) -> tuple[Path, str, int]:
    """Stream one validated PDF to disk and return its updated byte total."""
    original_name = Path(upload.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Eval batch uploads must be PDF files")

    stored_name = safe_filename(
        original_name,
        fallback="document.pdf",
        allowed_suffixes=PDF_SUFFIXES,
    )
    stored_name = unique_child_name(session_dir, stored_name, used_names)
    path = session_dir / stored_name
    try:
        file_total, request_total, first_bytes = (
            await await_cancellation_safe_blocking_call(
                asyncio.to_thread,
                _copy_pdf_upload,
                upload.file,
                path,
                original_name=original_name,
                request_total=request_total,
                reservation_id=reservation_id,
            )
        )
        if not upload_magic_matches(".pdf", first_bytes):
            raise HTTPException(
                status_code=400,
                detail=f"File {original_name} does not appear to be a valid PDF",
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, stored_name, request_total


def _resolve_prior_run_label(raw_label: object) -> tuple[Path, str]:
    """Resolve an eval selection beneath the configured output directory."""
    label = str(raw_label or "").strip()
    relative = Path(label)
    if (
        not label
        or relative.is_absolute()
        or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts)
    ):
        raise HTTPException(status_code=400, detail=f"Invalid run label: {label!r}")
    root = OUTPUTS_DIR.resolve()
    candidate = root / relative
    if candidate.is_symlink():
        raise HTTPException(status_code=400, detail=f"Invalid run label: {label!r}")
    run_dir = candidate.resolve()
    try:
        clean_label = run_dir.relative_to(root).as_posix()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run label: {label!r}") from exc
    if not run_dir.is_dir() or not (run_dir / "final_result.json").is_file():
        raise HTTPException(status_code=404, detail=f"Run not found: {label}")
    return run_dir, clean_label


def _prior_run_question_count(run_dir: Path, label: str) -> int:
    """Return the exact generated-item count from a prior run artifact."""
    result_path = run_dir / "final_result.json"
    try:
        if result_path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("final_result.json is unexpectedly large")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot preflight generated questions for run {label}: {exc}",
        ) from exc
    generated = payload.get("generated_questions") if isinstance(payload, dict) else None
    if not isinstance(generated, list) or not generated:
        raise HTTPException(
            status_code=400,
            detail=f"Run {label} has no generated questions to evaluate",
        )
    count = len(generated)
    per_run_limit = max_evaluation_items()
    if count > per_run_limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Run {label} contains {count} questions, exceeding "
                f"MAX_EVALUATION_ITEMS={per_run_limit}"
            ),
        )
    return count


def _require_grader_cost_confirmation(
    *,
    confirmed: bool,
    projected_items: int,
    grader_count: int,
    extraction_files: int = 0,
    extraction_calls: int = 0,
    bootstrap_calls: int = 0,
) -> int:
    """Require acknowledgement of every projected model-backed eval phase."""
    projected_calls = projected_items * grader_count
    if not confirmed:
        upload_projection = ""
        if extraction_files:
            upload_projection = (
                f" Upload mode also isolates {extraction_files} PDF(s), performs "
                f"{bootstrap_calls} document-dependency bootstrap call(s), and can "
                f"make up to {extraction_calls} Extracter agent call(s) "
                "(one retry per PDF). Safety moderation and content-classifier "
                "checks also run on every extracted PDF."
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit evaluation-cost confirmation is required. "
                f"This batch can submit {projected_items} item(s) to "
                f"{grader_count} graders ({projected_calls} projected grader calls)."
                f"{upload_projection}"
            ),
        )
    return projected_calls


def _forget_eval_batch_task(task: asyncio.Task, batch_id: str) -> None:
    """Remove a completed eval task and consume unexpected task failures."""
    eval_batch_tasks.pop(batch_id, None)
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.error(
                "Eval batch %s task finished with error (%s)",
                batch_id,
                safe_exception_category(error),
            )

@router.get("/api/eval-batch/outputs")
async def list_eval_outputs():
    """List output runs available for batch evaluation."""
    runs = _list_output_runs(limit=60)
    grader_count = len(question_level_criteria())
    return {
        "runs": runs,
        "total": len(runs),
        "policy": {
            "maxEntries": MAX_EVAL_SELECTIONS,
            "maxItems": MAX_EVAL_BATCH_ITEMS,
            "graderCount": grader_count,
            "graderMaxCompletionTokens": grader_max_completion_tokens(),
            "maxUploadExtractionFiles": MAX_UPLOAD_EXTRACTION_FILES,
            "maxUploadExtractionCalls": MAX_UPLOAD_EXTRACTION_CALLS,
            "maxUploadBootstrapCalls": MAX_UPLOAD_BOOTSTRAP_CALLS,
        },
    }

@router.post("/api/eval-batch/start")
@artifact_access_endpoint
async def start_eval_batch(
    mode: str = Form("prior_runs"),
    folders: str = Form("[]"),
    confirm_grader_calls: bool = Form(False),
    source_file: Optional[UploadFile] = File(None),
    generated_files: list[UploadFile] = File([]),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
):
    """Start a batch evaluation job."""
    validated_key = validate_idempotency_key(idempotency_key)
    batch_id = str(uuid.uuid4())
    session_dir: Path | None = None
    record_created = False
    scheduled = False
    upload_reservation_id = ""
    upload_reservation_held = False
    decision: IdempotencyDecision | None = None
    provider_checked = False
    paid_work_owner_id = f"eval-batch:{batch_id}"
    paid_work_lease_held = False
    all_uploads = ([source_file] if source_file is not None else []) + list(generated_files)
    try:
        _prune_eval_batch_jobs()
        existing_idempotency = await asyncio.to_thread(
            api_state.workflow_store.load_idempotency,
            validated_key,
        )
        if existing_idempotency is None:
            try:
                get_openai_client()
                provider_checked = True
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
            _ensure_batch_capacity()
        now = _workflow_now()

        if mode == "prior_runs":
            try:
                folder_list = json.loads(folders)
            except (TypeError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="folders must be a JSON array of folder names",
                ) from exc
            if not isinstance(folder_list, list):
                raise HTTPException(
                    status_code=400,
                    detail="folders must be a JSON array of folder names",
                )
            if not folder_list:
                raise HTTPException(status_code=400, detail="No folders selected")
            if len(folder_list) > MAX_EVAL_SELECTIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Maximum {MAX_EVAL_SELECTIONS} runs per batch",
                )

            entries = []
            prior_result_paths: list[Path] = []
            seen_labels: set[str] = set()
            projected_items = 0
            for folder_name in folder_list:
                run_dir, clean_label = _resolve_prior_run_label(folder_name)
                if clean_label in seen_labels:
                    continue
                seen_labels.add(clean_label)
                question_count = _prior_run_question_count(run_dir, clean_label)
                projected_items += question_count
                prior_result_paths.append(run_dir / "final_result.json")
                entries.append(
                    {
                        "name": clean_label,
                        "folder": clean_label,
                        "status": "queued",
                        "score": None,
                        "itemsEvaluated": 0,
                        "outputFolder": "",
                        "error": "",
                        "questionCount": question_count,
                    }
                )
            if projected_items > MAX_EVAL_BATCH_ITEMS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Selected runs contain {projected_items} questions, exceeding "
                        f"MAX_EVAL_BATCH_ITEMS={MAX_EVAL_BATCH_ITEMS}"
                    ),
                )
            grader_count = len(question_level_criteria())
            projected_grader_calls = _require_grader_cost_confirmation(
                confirmed=confirm_grader_calls,
                projected_items=projected_items,
                grader_count=grader_count,
            )

            batch = {
                "id": batch_id,
                "status": "starting",
                "mode": "prior_runs",
                "totalFiles": len(entries),
                "completedFiles": 0,
                "failedFiles": 0,
                "cancelledFiles": 0,
                "blockedFiles": 0,
                "doneFiles": 0,
                "outputFolder": "",
                "entries": entries,
                "error": "",
                "evaluationItemBudget": MAX_EVAL_BATCH_ITEMS,
                "projectedEvaluationItems": projected_items,
                "graderCount": grader_count,
                "graderMaxCompletionTokens": grader_max_completion_tokens(),
                "projectedGraderCalls": projected_grader_calls,
                "projectedExtractionFiles": 0,
                "projectedExtractionCalls": 0,
                "projectedBootstrapCalls": 0,
                "reservedEvaluationItems": 0,
                "created_at": now,
                "updated_at": now,
            }

        elif mode == "upload":
            if source_file is None:
                raise HTTPException(
                    status_code=400,
                    detail="source_file is required for upload mode",
                )
            if not generated_files:
                raise HTTPException(
                    status_code=400,
                    detail="At least one generated_file is required",
                )
            if len(generated_files) > MAX_EVAL_SELECTIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Maximum {MAX_EVAL_SELECTIONS} generated files",
                )
            grader_count = len(question_level_criteria())
            projected_items = MAX_EVAL_BATCH_ITEMS
            projected_extraction_files = len(generated_files) + 1
            projected_extraction_calls = projected_extraction_files * 2
            projected_bootstrap_calls = projected_extraction_files
            projected_grader_calls = _require_grader_cost_confirmation(
                confirmed=confirm_grader_calls,
                projected_items=projected_items,
                grader_count=grader_count,
                extraction_files=projected_extraction_files,
                extraction_calls=projected_extraction_calls,
                bootstrap_calls=projected_bootstrap_calls,
            )

            session_dir = UPLOADS_DIR / f"eval_batch_{batch_id}"
            upload_reservation_id = f"eval-batch:{batch_id}"
            projected_upload_bytes = _projected_pdf_upload_bytes(all_uploads)
            await upload_disk_budget.reserve_async(
                upload_reservation_id,
                root=UPLOADS_DIR,
                session_dir=session_dir,
                requested_bytes=projected_upload_bytes,
                capacity_bytes=UPLOAD_MAX_BYTES,
            )
            upload_reservation_held = True
            session_dir.mkdir(parents=True, exist_ok=False)
            used_names: set[str] = set()
            request_total = 0
            src_path, src_name, request_total = await _save_pdf_upload(
                source_file,
                session_dir=session_dir,
                used_names=used_names,
                request_total=request_total,
                reservation_id=upload_reservation_id,
            )

            entries = []
            for generated_file in generated_files:
                generated_path, generated_name, request_total = await _save_pdf_upload(
                    generated_file,
                    session_dir=session_dir,
                    used_names=used_names,
                    request_total=request_total,
                    reservation_id=upload_reservation_id,
                )
                entries.append(
                    {
                        "name": generated_name,
                        "folder": generated_name,
                        "pdf_path": str(generated_path),
                        "status": "queued",
                        "score": None,
                        "itemsEvaluated": 0,
                        "outputFolder": "",
                        "error": "",
                    }
                )

            batch = {
                "id": batch_id,
                "status": "starting",
                "mode": "upload",
                "source_pdf_path": str(src_path),
                "source_name": src_name,
                "totalFiles": len(entries),
                "completedFiles": 0,
                "failedFiles": 0,
                "cancelledFiles": 0,
                "blockedFiles": 0,
                "doneFiles": 0,
                "outputFolder": "",
                "entries": entries,
                "error": "",
                "evaluationItemBudget": MAX_EVAL_BATCH_ITEMS,
                "projectedEvaluationItems": projected_items,
                "graderCount": grader_count,
                "graderMaxCompletionTokens": grader_max_completion_tokens(),
                "projectedGraderCalls": projected_grader_calls,
                "projectedExtractionFiles": projected_extraction_files,
                "projectedExtractionCalls": projected_extraction_calls,
                "projectedBootstrapCalls": projected_bootstrap_calls,
                "reservedEvaluationItems": 0,
                "uploadFolder": session_dir.name,
                "created_at": now,
                "updated_at": now,
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

        response = {
            "success": True,
            "batchId": batch_id,
            "mode": mode,
            "totalFiles": len(entries),
            "projectedEvaluationItems": projected_items,
            "graderCount": grader_count,
            "graderMaxCompletionTokens": grader_max_completion_tokens(),
            "projectedGraderCalls": projected_grader_calls,
            "projectedExtractionFiles": batch["projectedExtractionFiles"],
            "projectedExtractionCalls": batch["projectedExtractionCalls"],
            "projectedBootstrapCalls": batch["projectedBootstrapCalls"],
        }
        if mode == "prior_runs":
            input_identity: dict[str, object] = {
                "runs": [
                    {
                        "folder": entry["folder"],
                        "finalResult": digest,
                    }
                    for entry, digest in zip(
                        entries,
                        await asyncio.gather(
                            *(
                                asyncio.to_thread(file_fingerprint, path)
                                for path in prior_result_paths
                            )
                        ),
                        strict=True,
                    )
                ],
            }
        else:
            upload_paths = [Path(batch["source_pdf_path"])] + [
                Path(entry["pdf_path"]) for entry in entries
            ]
            input_identity = {
                "uploads": list(
                    await asyncio.gather(
                        *(
                            asyncio.to_thread(file_fingerprint, path)
                            for path in upload_paths
                        )
                    )
                )
            }
        decision = await claim_paid_start(
            api_state.workflow_store,
            raw_key=validated_key,
            endpoint="/api/eval-batch/start",
            fingerprint=request_fingerprint(
                {
                    "mode": mode,
                    "confirmGraderCalls": bool(confirm_grader_calls),
                    **input_identity,
                }
            ),
            resource_kind=api_state.EVAL_BATCH_RECORD_KIND,
            resource_id=batch_id,
            response=response,
        )
        if not decision.owner:
            return decision.response

        if not provider_checked:
            try:
                get_openai_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        async with batch_admission_lock:
            _ensure_batch_capacity()
            _acquire_paid_work_lease_locked(paid_work_owner_id)
            paid_work_lease_held = True
            eval_batch_jobs[batch_id] = batch
            try:
                _persist_eval_batch(batch_id, batch)
            except BaseException:
                eval_batch_jobs.pop(batch_id, None)
                raise
            record_created = True

        async def _run_with_paid_work_lease() -> None:
            try:
                await run_eval_batch_async(batch_id)
            finally:
                await _release_paid_work_lease(paid_work_owner_id)

        task = asyncio.create_task(_run_with_paid_work_lease())
        eval_batch_tasks[batch_id] = task
        def _on_eval_batch_done(
            done_task: asyncio.Task,
            current_id: str = batch_id,
            current_owner_id: str = paid_work_owner_id,
        ) -> None:
            _forget_eval_batch_task(done_task, current_id)
            asyncio.create_task(
                _release_paid_work_lease(current_owner_id)
            )

        task.add_done_callback(_on_eval_batch_done)
        scheduled = True
        await commit_paid_start(api_state.workflow_store, decision)
        return response
    finally:
        for upload in all_uploads:
            try:
                await upload.close()
            except Exception as close_error:
                logger.warning(
                    "Failed to close an eval upload (%s)",
                    safe_exception_category(close_error),
                )
        if not scheduled and record_created:
            _discard_eval_batch_record(batch_id)
        if not scheduled and paid_work_lease_held:
            await _release_paid_work_lease(paid_work_owner_id)
        if not scheduled and session_dir is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        if upload_reservation_held:
            await upload_disk_budget.release_async(upload_reservation_id)
        if not scheduled:
            await abandon_paid_start(api_state.workflow_store, decision)

@router.get("/api/eval-batch/{batch_id}/status")
async def get_eval_batch_status(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    return _eval_batch_response(batch_id, batch)

@router.get("/api/eval-batch/{batch_id}/result")
async def get_eval_batch_result(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    if batch.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Batch not yet completed")
    return _eval_batch_response(batch_id, batch)

@router.post("/api/eval-batch/{batch_id}/stop")
async def stop_eval_batch(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    task = eval_batch_tasks.get(batch_id)
    if task and not task.done():
        task.cancel()
    if batch.get("status") not in TERMINAL_STATUSES:
        batch["status"] = "cancelled"
        batch["error"] = "Stopped by user"
        batch["cancel_requested"] = True
        for entry in batch.get("entries", []):
            if entry.get("status") not in TERMINAL_STATUSES:
                entry["status"] = "cancelled"
                entry["error"] = "Stopped by user"
        _refresh_eval_batch_progress(batch)
        try:
            _touch_eval_batch(batch)
        except Exception as exc:
            logger.error(
                "Could not persist eval batch %s cancellation (%s)",
                batch_id,
                safe_exception_category(exc),
            )
    return _eval_batch_response(batch_id, batch)


def _eval_batch_response(batch_id: str, batch: dict) -> dict:
    """Serialize an eval batch without mutating its persisted progress."""
    return {
        "batchId": batch_id,
        "status": batch.get("status"),
        "mode": batch.get("mode"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "blockedFiles": batch.get("blockedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "entries": _summarize_eval_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
        "projectedEvaluationItems": batch.get("projectedEvaluationItems", 0),
        "graderCount": batch.get("graderCount", 0),
        "graderMaxCompletionTokens": batch.get(
            "graderMaxCompletionTokens",
            grader_max_completion_tokens(),
        ),
        "projectedGraderCalls": batch.get("projectedGraderCalls", 0),
        "projectedExtractionFiles": batch.get("projectedExtractionFiles", 0),
        "projectedExtractionCalls": batch.get("projectedExtractionCalls", 0),
        "projectedBootstrapCalls": batch.get("projectedBootstrapCalls", 0),
        "reservedEvaluationItems": batch.get("reservedEvaluationItems", 0),
    }

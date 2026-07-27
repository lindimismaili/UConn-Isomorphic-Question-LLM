"""Create-data batch endpoints."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from agents.logger import logger
from fastapi import APIRouter, Form, Header, HTTPException

from backend.src.api import state as api_state
from backend.src.api.admin import require_service_available
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
    BATCH_WORKFLOW_CONCURRENCY,
    TERMINAL_STATUSES,
    SourceFileAdmissionError,
    _admit_create_data_batch,
    _discard_batch_record,
    _ensure_batch_capacity,
    _get_batch_record,
    _get_workflow_record,
    _list_evaluation_source_files,
    _make_unique_output_folder_name,
    _mark_workflow_cancelled,
    _parse_form_bool,
    _prune_batch_jobs,
    _prune_workflows,
    _refresh_batch_progress,
    _release_workflow_reservation,
    _sanitize_output_folder_name,
    _summarize_batch_entries,
    _touch_batch_job,
    _workflow_now,
    batch_tasks,
    get_openai_client,
    run_create_data_batch_async,
    validate_source_file_paths,
)
from backend.src.cost_controls import max_create_data_entries
from backend.src.paths import EVALUATION_FIXTURES_DIR as EVALUATION_DIR
from backend.src.safety import safe_exception_category, validate_instruction_text

router = APIRouter()

@router.post("/api/create-data/start")
@artifact_access_endpoint
async def start_create_data(
    prompt: str = Form(""),
    confirmWorkflowCosts: str = Form("false"),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
):
    """Start a batch workflow run for every file in data/evaluation."""
    require_service_available()
    try:
        prompt = validate_instruction_text(prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    validated_key = validate_idempotency_key(idempotency_key)
    _prune_workflows()
    _prune_batch_jobs()
    existing_idempotency = await asyncio.to_thread(
        api_state.workflow_store.load_idempotency,
        validated_key,
    )
    provider_checked = False

    evaluation_files = _list_evaluation_source_files()
    if not evaluation_files:
        raise HTTPException(status_code=404, detail="No evaluation files found")
    entry_limit = max_create_data_entries()
    if len(evaluation_files) > entry_limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Create Data contains {len(evaluation_files)} fixtures, exceeding "
                f"MAX_CREATE_DATA_ENTRIES={entry_limit}."
            ),
        )
    if existing_idempotency is None:
        _ensure_batch_capacity()
    if not _parse_form_bool(confirmWorkflowCosts, default=False):
        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit workflow-cost confirmation is required. "
                f"This request will start {len(evaluation_files)} full agentic workflows."
            ),
        )
    try:
        validated_paths = validate_source_file_paths(
            [str(path) for path in evaluation_files],
            max_files=entry_limit,
        )
    except SourceFileAdmissionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    evaluation_files = [Path(path) for path in validated_paths]
    if existing_idempotency is None:
        try:
            get_openai_client()
            provider_checked = True
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
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
                "relativePath": path.relative_to(
                    EVALUATION_DIR.resolve()
                ).as_posix(),
                "folderName": folder_name,
                "status": "queued",
                "workflowId": "",
                "outputFolder": "",
                "error": "",
            }
        )

    batch_id = str(uuid.uuid4())
    reservation_id = f"create-data:{batch_id}"
    workflow_slots = min(len(entries), BATCH_WORKFLOW_CONCURRENCY)
    now = _workflow_now()
    batch = {
        "id": batch_id,
        "status": "starting",
        "prompt": (prompt or "").strip(),
        "totalFiles": len(entries),
        "completedFiles": 0,
        "failedFiles": 0,
        "cancelledFiles": 0,
        "blockedFiles": 0,
        "doneFiles": 0,
        "outputFolder": "",
        "entries": entries,
        "summaryFile": "",
        "error": "",
        "projectedWorkflows": len(entries),
        "created_at": now,
        "updated_at": now,
    }
    response = {
        "success": True,
        "batchId": batch_id,
        "totalFiles": len(entries),
        "projectedWorkflows": len(entries),
    }
    fixture_digests = await asyncio.gather(
        *(
            asyncio.to_thread(file_fingerprint, path)
            for path in evaluation_files
        )
    )
    fixture_fingerprints = [
        {
            **digest,
            "relativePath": path.relative_to(
                EVALUATION_DIR.resolve()
            ).as_posix(),
        }
        for path, digest in zip(evaluation_files, fixture_digests, strict=True)
    ]
    decision: IdempotencyDecision | None = await claim_paid_start(
        api_state.workflow_store,
        raw_key=validated_key,
        endpoint="/api/create-data/start",
        fingerprint=request_fingerprint(
            {
                "prompt": (prompt or "").strip(),
                "confirmWorkflowCosts": True,
                "fixtures": fixture_fingerprints,
            }
        ),
        resource_kind="batch",
        resource_id=batch_id,
        response=response,
    )
    if not decision.owner:
        return decision.response

    record_created = False
    scheduled = False
    task: asyncio.Task | None = None
    try:
        if not provider_checked:
            try:
                get_openai_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        # Fast-fail for the owner; _admit_create_data_batch repeats this under
        # the shared admission lock and remains authoritative.
        _ensure_batch_capacity()
        await _admit_create_data_batch(
            batch_id,
            batch,
            reservation_id=reservation_id,
            workflow_slots=workflow_slots,
        )
        record_created = True
        task = asyncio.create_task(run_create_data_batch_async(batch_id))
        batch_tasks[batch_id] = task

        def _on_batch_done(
            _task: asyncio.Task,
            current_batch_id: str = batch_id,
            current_reservation_id: str = reservation_id,
        ) -> None:
            batch_tasks.pop(current_batch_id, None)
            # Idempotent fallback for cancellation before the batch coroutine
            # reaches its reservation-owning ``try/finally``.
            asyncio.create_task(
                _release_workflow_reservation(current_reservation_id)
            )

        task.add_done_callback(_on_batch_done)
        scheduled = True
        await commit_paid_start(api_state.workflow_store, decision)
        return response
    finally:
        if not scheduled and task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not scheduled and record_created:
            _discard_batch_record(batch_id)
        if not scheduled:
            await _release_workflow_reservation(reservation_id)
            await abandon_paid_start(api_state.workflow_store, decision)

@router.post("/api/create-data/{batch_id}/stop")
async def stop_create_data(batch_id: str):
    """Stop an active create-data batch run."""
    _prune_workflows()
    _prune_batch_jobs()
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    task = batch_tasks.get(batch_id)
    if task and not task.done():
        task.cancel()
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
                try:
                    _mark_workflow_cancelled(workflow)
                except Exception as exc:
                    logger.error(
                        "Could not persist child workflow %s cancellation (%s)",
                        workflow_id,
                        safe_exception_category(exc),
                    )
        _refresh_batch_progress(batch)
        try:
            _touch_batch_job(batch)
        except Exception as exc:
            logger.error(
                "Could not persist create-data batch %s cancellation (%s)",
                batch_id,
                safe_exception_category(exc),
            )

    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "blockedFiles": batch.get("blockedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "summaryFile": batch.get("summaryFile", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }

@router.get("/api/create-data/{batch_id}/status")
async def get_create_data_status(batch_id: str):
    """Get batch create-data progress."""
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "blockedFiles": batch.get("blockedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }

@router.get("/api/create-data/{batch_id}/result")
async def get_create_data_result(batch_id: str):
    """Get the final result of a create-data batch run."""
    batch = _get_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    if batch.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Batch job not yet completed")

    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "cancelledFiles": batch.get("cancelledFiles", 0),
        "blockedFiles": batch.get("blockedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "summaryFile": batch.get("summaryFile", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }

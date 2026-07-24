"""Create-data batch endpoints."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Form, HTTPException

from backend.src.api.admin import require_service_available
from backend.src.api.state import (
    TERMINAL_STATUSES,
    _get_batch_record,
    _get_workflow_record,
    _list_evaluation_source_files,
    _make_unique_output_folder_name,
    _mark_workflow_cancelled,
    _persist_batch,
    _prune_batch_jobs,
    _prune_workflows,
    _refresh_batch_progress,
    _sanitize_output_folder_name,
    _summarize_batch_entries,
    _touch_batch_job,
    _workflow_now,
    batch_jobs,
    batch_tasks,
    get_openai_client,
    run_create_data_batch_async,
)
from backend.src.paths import EVALUATION_FIXTURES_DIR as EVALUATION_DIR

router = APIRouter()

@router.post("/api/create-data/start")
async def start_create_data(prompt: str = Form("")):
    """Start a batch workflow run for every file in data/evaluation."""
    require_service_available()
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

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

@router.post("/api/create-data/{batch_id}/stop")
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

@router.get("/api/create-data/{batch_id}/status")
async def get_create_data_status(batch_id: str):
    """Get batch create-data progress."""
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

@router.get("/api/create-data/{batch_id}/result")
async def get_create_data_result(batch_id: str):
    """Get the final result of a create-data batch run."""
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

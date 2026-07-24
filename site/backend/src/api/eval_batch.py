"""Batch evaluation endpoints (eval-only runs over prior outputs or uploaded PDFs)."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.src.api.admin import require_service_available
from backend.src.api.state import (
    MAX_UPLOAD_BYTES,
    TERMINAL_STATUSES,
    _get_eval_batch_record,
    _list_output_runs,
    _prune_eval_batch_jobs,
    _refresh_eval_batch_progress,
    _summarize_eval_batch_entries,
    _touch_eval_batch,
    _workflow_now,
    eval_batch_jobs,
    eval_batch_tasks,
    get_openai_client,
    run_eval_batch_async,
)
from backend.src.paths import OUTPUTS_DIR, UPLOADS_DIR
from backend.src.safety import safe_filename, safe_stem

router = APIRouter()

MAX_EVAL_SELECTIONS = 20

@router.get("/api/eval-batch/outputs")
async def list_eval_outputs():
    """List output runs available for batch evaluation."""
    runs = _list_output_runs(limit=60)
    return {"runs": runs, "total": len(runs)}

@router.post("/api/eval-batch/start")
async def start_eval_batch(
    mode: str = Form("prior_runs"),
    folders: str = Form("[]"),
    source_file: Optional[UploadFile] = File(None),
    generated_files: list[UploadFile] = File([]),
):
    """Start a batch evaluation job."""
    require_service_available()
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    _prune_eval_batch_jobs()
    batch_id = str(uuid.uuid4())
    now = _workflow_now()

    if mode == "prior_runs":
        try:
            folder_list = json.loads(folders)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="folders must be a JSON array of folder names") from exc
        if not folder_list:
            raise HTTPException(status_code=400, detail="No folders selected")
        if len(folder_list) > MAX_EVAL_SELECTIONS:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_EVAL_SELECTIONS} runs per batch")

        entries = []
        for folder_name in folder_list:
            clean = safe_stem(str(folder_name), fallback="")
            if not clean:
                continue
            run_dir = OUTPUTS_DIR / clean
            if not run_dir.is_dir() or not (run_dir / "final_result.json").exists():
                raise HTTPException(status_code=404, detail=f"Run not found: {folder_name}")
            entries.append({"name": clean, "folder": clean, "status": "queued", "score": None, "itemsEvaluated": 0, "outputFolder": "", "error": ""})

        batch = {"status": "starting", "mode": "prior_runs", "totalFiles": len(entries), "completedFiles": 0, "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 0, "outputFolder": "", "entries": entries, "error": "", "created_at": now, "updated_at": now}

    elif mode == "upload":
        if source_file is None:
            raise HTTPException(status_code=400, detail="source_file is required for upload mode")
        if not generated_files:
            raise HTTPException(status_code=400, detail="At least one generated_file is required")
        if len(generated_files) > MAX_EVAL_SELECTIONS:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_EVAL_SELECTIONS} generated files")

        session_dir = UPLOADS_DIR / f"eval_batch_{batch_id}"
        session_dir.mkdir(parents=True, exist_ok=True)

        # Save source PDF
        src_content = await source_file.read()
        if len(src_content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Source file too large")
        src_name = safe_filename(source_file.filename or "source.pdf", fallback="source.pdf")
        src_path = session_dir / src_name
        src_path.write_bytes(src_content)

        # Save generated PDFs
        entries = []
        for gen_file in generated_files:
            gen_content = await gen_file.read()
            if len(gen_content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"Generated file {gen_file.filename} too large")
            gen_name = safe_filename(gen_file.filename or "generated.pdf", fallback="generated.pdf")
            gen_path = session_dir / gen_name
            gen_path.write_bytes(gen_content)
            entries.append({"name": gen_name, "folder": gen_name, "pdf_path": str(gen_path), "status": "queued", "score": None, "itemsEvaluated": 0, "outputFolder": "", "error": ""})

        batch = {"status": "starting", "mode": "upload", "source_pdf_path": str(src_path), "source_name": src_name, "totalFiles": len(entries), "completedFiles": 0, "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 0, "outputFolder": "", "entries": entries, "error": "", "created_at": now, "updated_at": now}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    eval_batch_jobs[batch_id] = batch
    task = asyncio.create_task(run_eval_batch_async(batch_id))
    eval_batch_tasks[batch_id] = task
    task.add_done_callback(lambda _t, bid=batch_id: eval_batch_tasks.pop(bid, None))

    return {"success": True, "batchId": batch_id, "mode": mode, "totalFiles": len(entries)}

@router.get("/api/eval-batch/{batch_id}/status")
async def get_eval_batch_status(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    _refresh_eval_batch_progress(batch)
    _touch_eval_batch(batch)
    return {"batchId": batch_id, "status": batch.get("status"), "mode": batch.get("mode"), "totalFiles": batch.get("totalFiles",0), "completedFiles": batch.get("completedFiles",0), "failedFiles": batch.get("failedFiles",0), "cancelledFiles": batch.get("cancelledFiles",0), "doneFiles": batch.get("doneFiles",0), "outputFolder": batch.get("outputFolder",""), "entries": _summarize_eval_batch_entries(batch.get("entries",[])), "error": batch.get("error","")}

@router.get("/api/eval-batch/{batch_id}/result")
async def get_eval_batch_result(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    if batch.get("status") not in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Batch not yet completed")
    _refresh_eval_batch_progress(batch)
    return {"batchId": batch_id, "status": batch.get("status"), "mode": batch.get("mode"), "totalFiles": batch.get("totalFiles",0), "completedFiles": batch.get("completedFiles",0), "failedFiles": batch.get("failedFiles",0), "cancelledFiles": batch.get("cancelledFiles",0), "doneFiles": batch.get("doneFiles",0), "outputFolder": batch.get("outputFolder",""), "entries": _summarize_eval_batch_entries(batch.get("entries",[])), "error": batch.get("error","")}

@router.post("/api/eval-batch/{batch_id}/stop")
async def stop_eval_batch(batch_id: str):
    batch = _get_eval_batch_record(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Eval batch not found")
    if batch.get("status") not in TERMINAL_STATUSES:
        batch["status"] = "cancelled"
        batch["error"] = "Stopped by user"
        batch["cancel_requested"] = True
        for entry in batch.get("entries", []):
            if entry.get("status") not in TERMINAL_STATUSES:
                entry["status"] = "cancelled"
                entry["error"] = "Stopped by user"
        _refresh_eval_batch_progress(batch)
        _touch_eval_batch(batch)
    task = eval_batch_tasks.pop(batch_id, None)
    if task and not task.done():
        task.cancel()
    return {"batchId": batch_id, "status": batch.get("status"), "totalFiles": batch.get("totalFiles",0), "doneFiles": batch.get("doneFiles",0), "error": batch.get("error","")}

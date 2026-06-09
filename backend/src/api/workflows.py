"""Workflow lifecycle endpoints: start, status, result, rerun, stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.src.api.state import (
    ALLOWED_AGENT_INPUT_EXTENSIONS,
    ALLOWED_UPLOAD_EXTENSIONS,
    INPUT_TO_AGENT_SEQUENCE,
    SOURCE_FILE_INPUT_AGENTS,
    TERMINAL_STATUSES,
    _get_workflow_record,
    _is_under_directory,
    _new_workflow_record,
    _normalize_agent_key,
    _parse_agent_input_upload,
    _parse_form_bool,
    _persist_workflow,
    _prune_batch_jobs,
    _prune_workflows,
    _register_workflow_task,
    _relative_output_folder,
    _resolve_output_folder_label,
    _save_input_to_agent_source_file,
    _source_paths_from_session,
    _touch_workflow,
    _validate_input_to_agent_payload,
    _workflow_status_payload,
    get_openai_client,
    run_workflow_async,
    run_workflow_from_agent_async,
    run_workflow_from_agent_input_async,
    workflows,
)
from backend.src.artifacts import WORKFLOW_AGENT_SEQUENCE, load_source_file_paths
from backend.src.paths import EVALUATION_FIXTURES_DIR as EVALUATION_DIR

router = APIRouter()

@router.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _touch_workflow(workflow)
    return _workflow_status_payload(workflow_id, workflow)

@router.get("/api/workflow/{workflow_id}/result")
async def get_workflow_result(workflow_id: str):
    """Get the final result of a workflow."""
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

@router.post("/api/workflow/start")
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
        raise HTTPException(status_code=503, detail=str(e)) from e

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

@router.post("/api/workflow/rerun-from-agent")
async def rerun_workflow_from_agent(
    sourceOutputFolder: str = Form(...),
    startAgent: str = Form(...),
    prompt: str = Form(""),
):
    """Start a new workflow by reusing cached outputs before startAgent."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

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
    if start_agent in SOURCE_FILE_INPUT_AGENTS and not file_paths:
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

@router.post("/api/workflow/start-from-agent-input")
async def start_workflow_from_agent_input(
    startAgent: str = Form(...),
    prompt: str = Form(""),
    agentInput: UploadFile = File(...),
):
    """Start a workflow from one agent using an uploaded source or agent input file."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

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

@router.get("/api/workflow/{workflow_id}/stream")
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

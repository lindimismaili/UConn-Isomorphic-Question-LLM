"""Workflow lifecycle endpoints: start, status, result, rerun, stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from agents.logger import logger
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.src.api import state as api_state
from backend.src.api.admin import require_service_available
from backend.src.api.artifact_access import artifact_access_endpoint
from backend.src.api.idempotency import (
    IdempotencyDecision,
    abandon_paid_start,
    bytes_fingerprint,
    claim_paid_start,
    commit_paid_start,
    file_fingerprint,
    paid_start_key_lock,
    request_fingerprint,
    validate_idempotency_key,
)
from backend.src.api.state import (
    ALLOWED_AGENT_INPUT_EXTENSIONS,
    INPUT_TO_AGENT_SEQUENCE,
    SOURCE_FILE_INPUT_AGENTS,
    TERMINAL_STATUSES,
    SourceFileAdmissionError,
    _admit_workflow_record,
    _discard_input_to_agent_upload,
    _discard_workflow_record,
    _get_workflow_record,
    _mark_workflow_cancelled,
    _new_workflow_record,
    _normalize_agent_key,
    _parse_agent_input_upload,
    _parse_form_bool,
    _prune_batch_jobs,
    _prune_workflows,
    _read_upload_limited,
    _register_workflow_task,
    _relative_output_folder,
    _release_workflow_reservation,
    _reserve_workflow_capacity,
    _resolve_evaluation_fixture_paths,
    _resolve_output_folder_label,
    _run_with_workflow_reservation,
    _save_input_to_agent_source_file,
    _source_paths_from_session,
    _validate_input_to_agent_payload,
    _workflow_status_payload,
    get_openai_client,
    run_workflow_async,
    run_workflow_from_agent_async,
    run_workflow_from_agent_input_async,
    validate_source_file_paths,
    workflow_tasks,
    workflow_token_queues,
)
from backend.src.api.upload_budget import upload_disk_budget
from backend.src.artifacts import load_agent_output_payload, load_source_file_paths
from backend.src.cost_controls import grader_max_completion_tokens, max_evaluation_items
from backend.src.evaluation import question_level_criteria
from backend.src.provider_timeouts import await_cancellation_safe_blocking_call
from backend.src.registry import canonical_theme
from backend.src.registry import evaluation_agent as _evaluation_agent
from backend.src.safety import safe_exception_category, validate_instruction_text

router = APIRouter()


def _validated_prompt(prompt: str) -> str:
    """Bound and normalize a prompt before provider, persistence, or hashing."""
    try:
        return validate_instruction_text(prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _request_file_fingerprints(paths: list[str]) -> list[dict[str, Any]]:
    """Hash resolved local request inputs without blocking the event loop."""
    return list(
        await asyncio.gather(
            *(asyncio.to_thread(file_fingerprint, Path(path)) for path in paths)
        )
    )


async def _resume_output_fingerprints(
    source_dir: Path,
    start_agent: str,
) -> list[dict[str, str]]:
    """Hash every cached upstream output consumed by a rerun."""
    start_index = INPUT_TO_AGENT_SEQUENCE.index(start_agent)
    upstream_agents = INPUT_TO_AGENT_SEQUENCE[:start_index]

    def fingerprint_output(agent_key: str) -> dict[str, str]:
        payload = load_agent_output_payload(source_dir, agent_key)
        return {
            "agent": agent_key,
            "sha256": request_fingerprint(payload),
        }

    return list(
        await asyncio.gather(
            *(
                asyncio.to_thread(fingerprint_output, agent_key)
                for agent_key in upstream_agents
            )
        )
    )


def _require_evaluation_cost_confirmation(
    *,
    run_evaluation: bool,
    confirmed: bool,
) -> int:
    """Require explicit consent for the evaluator's multi-grader fan-out."""
    if not run_evaluation:
        return 0
    projected_calls = max_evaluation_items() * len(question_level_criteria())
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail=(
                "runEvaluation=true requires confirmEvaluationCost=true. "
                f"A run can submit up to {max_evaluation_items()} items to "
                f"{len(question_level_criteria())} graders "
                f"({projected_calls} maximum grader calls), capped at "
                f"{grader_max_completion_tokens()} generated tokens per grader call."
            ),
        )
    return projected_calls

@router.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _workflow_status_payload(workflow_id, workflow)

@router.get("/api/workflow/{workflow_id}/result")
async def get_workflow_result(workflow_id: str):
    """Get the final result of a workflow."""
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Workflow not yet completed")

    return {
        "workflowId": workflow_id,
        "result": workflow.get("result"),
        "agents": workflow.get("agents", {}),
        "files": workflow.get("files", []),
        "outputFolder": workflow.get("outputFolder", ""),
        "usage": workflow.get("usage", {}),
    }


@router.post("/api/workflow/{workflow_id}/stop")
async def stop_workflow(workflow_id: str):
    """Idempotently cancel a process-local workflow and persist its status."""
    workflow = _get_workflow_record(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow.get("status") not in TERMINAL_STATUSES:
        task = workflow_tasks.get(workflow_id)
        if task is not None and not task.done():
            task.cancel()
        try:
            _mark_workflow_cancelled(workflow)
        except Exception as exc:
            logger.error(
                "Could not persist workflow %s cancellation (%s)",
                workflow_id,
                safe_exception_category(exc),
            )

    return _workflow_status_payload(workflow_id, workflow)


@router.post("/api/workflow/start")
@artifact_access_endpoint
async def start_workflow(
    prompt: str = Form(""),
    sessionId: str = Form(""),
    fileIds: str = Form("[]"),
    testFiles: str = Form("[]"),
    runEvaluation: str = Form("false"),
    confirmEvaluationCost: str = Form("false"),
    theme: str = Form(""),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
):
    """Start a new workflow and return a stream of status updates."""
    require_service_available()
    prompt = _validated_prompt(prompt)
    validated_key = validate_idempotency_key(idempotency_key)
    try:
        resolved_theme = canonical_theme(theme)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider_checked = False
    existing_idempotency = await asyncio.to_thread(
        api_state.workflow_store.load_idempotency,
        validated_key,
    )
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
    file_paths.extend(_resolve_evaluation_fixture_paths(raw_test_files, strict=False))

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
    try:
        file_paths = validate_source_file_paths(deduped_file_paths)
    except SourceFileAdmissionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc

    run_evaluation = _parse_form_bool(runEvaluation, default=False)
    projected_grader_calls = _require_evaluation_cost_confirmation(
        run_evaluation=run_evaluation,
        confirmed=_parse_form_bool(confirmEvaluationCost, default=False),
    )
    if existing_idempotency is None:
        try:
            get_openai_client()
            provider_checked = True
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    response = {
        "success": True,
        "workflowId": workflow_id,
        "projectedGraderCalls": projected_grader_calls,
        "graderMaxCompletionTokens": grader_max_completion_tokens(),
    }
    fingerprint = request_fingerprint(
        {
            "prompt": prompt,
            "files": await _request_file_fingerprints(file_paths),
            "runEvaluation": run_evaluation,
            "theme": resolved_theme,
        }
    )
    decision: IdempotencyDecision | None = await claim_paid_start(
        api_state.workflow_store,
        raw_key=validated_key,
        endpoint="/api/workflow/start",
        fingerprint=fingerprint,
        resource_kind="workflow",
        resource_id=workflow_id,
        response=response,
    )
    if not decision.owner:
        return decision.response

    reservation_id = f"workflow:{workflow_id}"
    reservation_held = False
    record_created = False
    scheduled = False
    try:
        if not provider_checked:
            try:
                get_openai_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        await _reserve_workflow_capacity(reservation_id)
        reservation_held = True

        workflow = _new_workflow_record()
        workflow["projectedGraderCalls"] = projected_grader_calls
        workflow["graderMaxCompletionTokens"] = grader_max_completion_tokens()
        if sessionId:
            workflow["uploadFolder"] = sessionId
        await _admit_workflow_record(
            workflow_id,
            workflow,
            reservation_id=reservation_id,
        )
        record_created = True

        _register_workflow_task(
            workflow_id,
            _run_with_workflow_reservation(
                reservation_id,
                lambda: run_workflow_async(
                    workflow_id,
                    prompt,
                    file_paths,
                    run_evaluation=run_evaluation,
                    theme=resolved_theme,
                ),
            ),
        )
        scheduled = True
        await commit_paid_start(api_state.workflow_store, decision)
        return response
    finally:
        if not scheduled and record_created:
            _discard_workflow_record(workflow_id)
        if not scheduled and reservation_held:
            await _release_workflow_reservation(reservation_id)
        if not scheduled:
            await abandon_paid_start(api_state.workflow_store, decision)

@router.post("/api/workflow/rerun-from-agent")
@artifact_access_endpoint
async def rerun_workflow_from_agent(
    sourceOutputFolder: str = Form(...),
    startAgent: str = Form(...),
    prompt: str = Form(""),
    runEvaluation: str = Form("false"),
    confirmEvaluationCost: str = Form("false"),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
):
    """Start a new workflow by reusing cached outputs before startAgent."""
    require_service_available()
    prompt = _validated_prompt(prompt)
    validated_key = validate_idempotency_key(idempotency_key)
    provider_checked = False
    existing_idempotency = await asyncio.to_thread(
        api_state.workflow_store.load_idempotency,
        validated_key,
    )
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
    run_evaluation = _parse_form_bool(runEvaluation, default=False)
    projected_grader_calls = _require_evaluation_cost_confirmation(
        run_evaluation=run_evaluation,
        confirmed=_parse_form_bool(confirmEvaluationCost, default=False),
    )
    if start_agent == _evaluation_agent() and not run_evaluation:
        raise HTTPException(
            status_code=400,
            detail="Starting from evaluation requires runEvaluation=true",
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
    if file_paths:
        try:
            file_paths = validate_source_file_paths(file_paths)
        except SourceFileAdmissionError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
            ) from exc
    if existing_idempotency is None:
        try:
            get_openai_client()
            provider_checked = True
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    workflow_id = str(uuid.uuid4())
    response = {
        "success": True,
        "workflowId": workflow_id,
        "startAgent": start_agent,
        "sourceOutputFolder": source_label,
        "projectedGraderCalls": projected_grader_calls,
        "graderMaxCompletionTokens": grader_max_completion_tokens(),
    }
    fingerprint = request_fingerprint(
        {
            "sourceOutputFolder": source_label,
            "startAgent": start_agent,
            "prompt": prompt,
            "sourceFiles": await _request_file_fingerprints(file_paths),
            "upstreamOutputs": await _resume_output_fingerprints(
                source_dir,
                start_agent,
            ),
            "runEvaluation": run_evaluation,
        }
    )
    decision: IdempotencyDecision | None = await claim_paid_start(
        api_state.workflow_store,
        raw_key=validated_key,
        endpoint="/api/workflow/rerun-from-agent",
        fingerprint=fingerprint,
        resource_kind="workflow",
        resource_id=workflow_id,
        response=response,
    )
    if not decision.owner:
        return decision.response

    reservation_id = f"workflow:{workflow_id}"
    reservation_held = False
    record_created = False
    scheduled = False
    try:
        if not provider_checked:
            try:
                get_openai_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        await _reserve_workflow_capacity(reservation_id)
        reservation_held = True

        workflow = _new_workflow_record()
        workflow["resumedFrom"] = {
            "outputFolder": source_label,
            "startAgent": start_agent,
        }
        workflow["projectedGraderCalls"] = projected_grader_calls
        workflow["graderMaxCompletionTokens"] = grader_max_completion_tokens()
        await _admit_workflow_record(
            workflow_id,
            workflow,
            reservation_id=reservation_id,
        )
        record_created = True

        _register_workflow_task(
            workflow_id,
            _run_with_workflow_reservation(
                reservation_id,
                lambda: run_workflow_from_agent_async(
                    workflow_id,
                    prompt,
                    file_paths,
                    start_agent=start_agent,
                    resume_source_dir=source_dir,
                    resume_source_label=source_label,
                    run_evaluation=run_evaluation,
                ),
            ),
        )
        scheduled = True
        await commit_paid_start(api_state.workflow_store, decision)
        return response
    finally:
        if not scheduled and record_created:
            _discard_workflow_record(workflow_id)
        if not scheduled and reservation_held:
            await _release_workflow_reservation(reservation_id)
        if not scheduled:
            await abandon_paid_start(api_state.workflow_store, decision)

@router.post("/api/workflow/start-from-agent-input")
@artifact_access_endpoint
async def start_workflow_from_agent_input(
    startAgent: str = Form(...),
    prompt: str = Form(""),
    runEvaluation: str = Form("false"),
    confirmEvaluationCost: str = Form("false"),
    agentInput: UploadFile = File(...),
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
):
    """Start a workflow from one agent using an uploaded source or agent input file."""
    require_service_available()
    workflow_id = str(uuid.uuid4())
    reservation_id = f"workflow:{workflow_id}"
    reservation_held = False
    upload_reservation_id = ""
    upload_reservation_held = False
    record_created = False
    scheduled = False
    decision: IdempotencyDecision | None = None
    key_lock: asyncio.Lock | None = None
    key_lock_held = False
    provider_checked = False
    try:
        prompt = _validated_prompt(prompt)
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
        run_evaluation = _parse_form_bool(runEvaluation, default=False)
        projected_grader_calls = _require_evaluation_cost_confirmation(
            run_evaluation=run_evaluation,
            confirmed=_parse_form_bool(confirmEvaluationCost, default=False),
        )
        if start_agent == _evaluation_agent() and not run_evaluation:
            raise HTTPException(
                status_code=400,
                detail="Starting from evaluation requires runEvaluation=true",
            )

        validated_key, key_lock = paid_start_key_lock(idempotency_key)
        await key_lock.acquire()
        key_lock_held = True
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
            # Preserve the paid-work admission invariant across the awaited
            # upload read. Same-key duplicates serialize above and bypass this
            # reservation once the first request has a durable mapping.
            await _reserve_workflow_capacity(reservation_id)
            reservation_held = True

        filename = Path(agentInput.filename or "agent_input.txt").name
        suffix = Path(filename).suffix.lower()
        content = await _read_upload_limited(agentInput, label="Agent input file")

        source_file_paths: list[str] = []
        agent_input_payload: Any = None
        input_kind = "agent_input"
        if start_agent in SOURCE_FILE_INPUT_AGENTS:
            if suffix not in api_state.ALLOWED_UPLOAD_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Normalizer and Extracter starts need a source file with one of: "
                        + ", ".join(sorted(api_state.ALLOWED_UPLOAD_EXTENSIONS))
                    ),
                )
            if (
                suffix in api_state.UPLOAD_MAGIC_SIGNATURES
                and not api_state.upload_magic_matches(suffix, content[:1024])
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Source file does not appear to be a valid {suffix} file",
                )
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

        response = {
            "success": True,
            "workflowId": workflow_id,
            "startAgent": start_agent,
            "inputFilename": filename,
            "inputKind": input_kind,
            "projectedGraderCalls": projected_grader_calls,
            "graderMaxCompletionTokens": grader_max_completion_tokens(),
        }
        decision = await claim_paid_start(
            api_state.workflow_store,
            raw_key=validated_key,
            endpoint="/api/workflow/start-from-agent-input",
            fingerprint=request_fingerprint(
                {
                    "startAgent": start_agent,
                    "prompt": prompt,
                    "runEvaluation": run_evaluation,
                    "inputFilename": filename,
                    "inputKind": input_kind,
                    "inputSha256": bytes_fingerprint(content),
                }
            ),
            resource_kind="workflow",
            resource_id=workflow_id,
            response=response,
        )
        if not decision.owner:
            return decision.response

        if not provider_checked:
            try:
                get_openai_client()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        # A prior pending owner can abandon its claim before admission. If this
        # request then becomes the owner, acquire the slot it previously
        # skipped while validating the duplicate body.
        if not reservation_held:
            await _reserve_workflow_capacity(reservation_id)
            reservation_held = True

        if input_kind == "source_file":
            upload_reservation_id = f"input-to-agent:{workflow_id}"
            upload_session_dir = api_state.UPLOADS_DIR / f"input_to_agent_{workflow_id}"
            await upload_disk_budget.reserve_async(
                upload_reservation_id,
                root=api_state.UPLOADS_DIR,
                session_dir=upload_session_dir,
                requested_bytes=len(content),
                capacity_bytes=api_state.UPLOAD_MAX_BYTES,
            )
            upload_reservation_held = True
            source_path = await await_cancellation_safe_blocking_call(
                asyncio.to_thread,
                _save_input_to_agent_source_file,
                workflow_id=workflow_id,
                filename=filename,
                content=content,
            )
            source_file_paths.append(str(source_path))

        workflow = _new_workflow_record()
        workflow["inputToAgent"] = {
            "startAgent": start_agent,
            "inputFilename": filename,
            "inputKind": input_kind,
        }
        if input_kind == "source_file":
            workflow["uploadFolder"] = f"input_to_agent_{workflow_id}"
        workflow["projectedGraderCalls"] = projected_grader_calls
        workflow["graderMaxCompletionTokens"] = grader_max_completion_tokens()
        await _admit_workflow_record(
            workflow_id,
            workflow,
            reservation_id=reservation_id,
        )
        record_created = True

        _register_workflow_task(
            workflow_id,
            _run_with_workflow_reservation(
                reservation_id,
                lambda: run_workflow_from_agent_input_async(
                    workflow_id,
                    prompt,
                    file_paths=source_file_paths,
                    start_agent=start_agent,
                    agent_input_payload=agent_input_payload,
                    input_filename=filename,
                    run_evaluation=run_evaluation,
                ),
            ),
        )
        scheduled = True
        await commit_paid_start(api_state.workflow_store, decision)
        return response
    finally:
        try:
            await agentInput.close()
        except Exception as close_error:
            logger.warning(
                "Failed to close an agent input upload (%s)",
                type(close_error).__name__,
            )
        if not scheduled and record_created:
            _discard_workflow_record(workflow_id)
        if not scheduled and reservation_held:
            await _release_workflow_reservation(reservation_id)
        if not scheduled:
            _discard_input_to_agent_upload(workflow_id)
            await abandon_paid_start(api_state.workflow_store, decision)
        if upload_reservation_held:
            await upload_disk_budget.release_async(upload_reservation_id)
        if key_lock_held and key_lock is not None:
            key_lock.release()

@router.get("/api/workflow/{workflow_id}/stream")
async def stream_workflow(workflow_id: str):
    """Stream live token deltas + status updates for a workflow over SSE.

    Emits three named event types:
      - ``token``  data ``{"agent": "<registry key>", "delta": "<text chunk>"}``
        — the live "matrix" text effect the frontend renders on the active node.
      - ``status`` data = the same payload as ``GET /status`` (sent on change).
      - ``done``   data ``{}`` — once the workflow reaches a terminal state.

    Token streaming is best-effort/cosmetic; polling ``/status`` stays the
    authoritative progress channel, so a dropped/absent stream never loses state.
    """
    if _get_workflow_record(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    async def generate_events():
        # The engine's per-workflow token queue may not exist yet (stream opened
        # before the run task started) or may already be gone (run finished); both
        # are handled — we re-fetch until we hold a reference, then drain it.
        queue = workflow_token_queues.get(workflow_id)
        last_status = None
        while True:
            if queue is None:
                queue = workflow_token_queues.get(workflow_id)
            workflow = _get_workflow_record(workflow_id)
            if not workflow:
                yield "event: done\ndata: {}\n\n"
                break

            if queue is not None:
                for _ in range(100):
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield f"event: token\ndata: {json.dumps(item)}\n\n"

            current_status = json.dumps(_workflow_status_payload(workflow_id, workflow))
            if current_status != last_status:
                yield f"event: status\ndata: {current_status}\n\n"
                last_status = current_status

            if workflow.get("status") in TERMINAL_STATUSES:
                if queue is not None:
                    while True:
                        try:
                            item = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        yield f"event: token\ndata: {json.dumps(item)}\n\n"
                yield "event: done\ndata: {}\n\n"
                break

            await asyncio.sleep(0.1)

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

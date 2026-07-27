"""Read-only config endpoints: dropdown, rubric, agents, prompts, eval files."""

from __future__ import annotations

import json
import os
from typing import Any

from agents.logger import logger
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from backend.src.api.state import (
    _list_evaluation_source_files,
)
from backend.src.auth import validated_paypal_donate_url
from backend.src.cost_controls import (
    grader_max_completion_tokens,
    max_create_data_entries,
    max_evaluation_items,
)
from backend.src.evaluation import question_level_criteria
from backend.src.helpers import resolve_prompt_path
from backend.src.paths import EVALUATION_FIXTURES_DIR as EVALUATION_DIR
from backend.src.paths import (
    PROMPTS_DIR,
    RUBRIC_PATH,
)
from backend.src.registry import all_agents as _all_agents
from backend.src.registry import promptable_agents as _promptable_agents
from backend.src.registry import themes as _themes
from backend.src.safety import MAX_INSTRUCTION_CHARS, safe_exception_category

router = APIRouter()


@router.get("/api/public-config")
async def get_public_config(request: Request):
    """Return the small public configuration needed before authentication."""
    payload: dict[str, Any] = {
        "authRequired": bool(getattr(request.app.state, "auth_required", False)),
        "maxInstructionChars": MAX_INSTRUCTION_CHARS,
        "evaluationPolicy": {
            "maxItems": max_evaluation_items(),
            "graderCount": len(question_level_criteria()),
            "graderMaxCompletionTokens": grader_max_completion_tokens(),
        },
        "createDataPolicy": {
            "maxEntries": max_create_data_entries(),
        },
    }
    donate_url = validated_paypal_donate_url(os.getenv("PAYPAL_DONATE_URL"))
    if donate_url is not None:
        payload["paypalDonateUrl"] = donate_url
    return payload


@router.get("/api/dropdown")
async def get_dropdown():
    """Get the prompt dropdown configuration for the frontend."""
    try:
        return {"items": _themes()}
    except Exception as exc:
        logger.error(
            "Failed to load dropdown configuration (%s)",
            safe_exception_category(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to load dropdown configuration",
        ) from exc

@router.get("/api/rubric")
async def get_rubric():
    """Get the evaluation rubric configuration for the frontend."""
    if not RUBRIC_PATH.exists():
        raise HTTPException(status_code=404, detail="Rubric configuration not found")

    try:
        rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
        if not isinstance(rubric, dict):
            raise ValueError("Rubric root must be a JSON object")
        return rubric
    except Exception as exc:
        logger.error(
            "Failed to load rubric configuration (%s)",
            safe_exception_category(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to load rubric configuration",
        ) from exc

@router.get("/api/agents")
async def get_agents():
    """Get the agents configuration for the frontend."""
    try:
        return _all_agents()
    except Exception as exc:
        logger.error(
            "Failed to load agents configuration (%s)",
            safe_exception_category(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to load agents configuration",
        ) from exc

@router.get("/api/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """Get an agent prompt file content."""
    # Validate prompt name to prevent path traversal
    allowed_prompts = _promptable_agents()
    if prompt_name not in allowed_prompts:
        raise HTTPException(status_code=404, detail="Prompt not found")

    prompt_path = resolve_prompt_path(PROMPTS_DIR, prompt_name)
    if prompt_path is None:
        raise HTTPException(status_code=404, detail="Prompt file not found")

    try:
        return PlainTextResponse(prompt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(
            "Failed to load prompt %s (%s)",
            prompt_name,
            safe_exception_category(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to load prompt",
        ) from exc

@router.get("/api/evaluation/files")
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
    except Exception as exc:
        logger.error(
            "Failed to load evaluation files (%s)",
            safe_exception_category(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to load evaluation files",
        ) from exc

    return {"files": files}

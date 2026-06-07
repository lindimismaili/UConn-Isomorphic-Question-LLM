"""Read-only config endpoints: dropdown, rubric, agents, prompts, eval files."""

from __future__ import annotations

import json
from typing import Any

from agents.logger import logger
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from backend.src.api.state import (
    _list_evaluation_source_files,
)
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.helpers import resolve_prompt_path
from backend.src.paths import (
    AGENTS_CONFIG_PATH,
    DROPDOWN_PATH,
    PROMPTS_DIR,
    RUBRIC_PATH,
)
from backend.src.paths import EVALUATION_FIXTURES_DIR as EVALUATION_DIR

router = APIRouter()

@router.get("/api/dropdown")
async def get_dropdown():
    """Get the prompt dropdown configuration for the frontend."""
    if not DROPDOWN_PATH.exists():
        raise HTTPException(status_code=404, detail="Dropdown configuration not found")

    try:
        return json.loads(DROPDOWN_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load dropdown configuration: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load dropdown configuration") from e

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
    except Exception as e:
        logger.error("Failed to load rubric configuration: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load rubric configuration") from e

@router.get("/api/agents")
async def get_agents():
    """Get the agents configuration for the frontend."""
    if not AGENTS_CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="Agents configuration not found")

    try:
        agents = json.loads(AGENTS_CONFIG_PATH.read_text(encoding="utf-8"))
        rubric_model = _load_rubric_model()

        return {
            "setup": {
                "name": "Starter",
                "description": "Prepares the shared workflow runtime",
                "model": "OpenAI Runtime",
                "reasoning": "none",
                "kind": "runtime",
                "inputFrom": "User",
                "outputTo": "Normalizer",
            },
            **agents,
            "evaluation": {
                "name": "Evaluator",
                "description": "Scores generated outputs and writes evaluator artifacts",
                "model": rubric_model,
                "reasoning": "low",
                "kind": "agent",
                "inputFrom": "Latexifier",
                "outputTo": "Output",
            },
            "output": {
                "name": "Output",
                "description": "Finalizes workflow files and result metadata",
                "model": "Workflow Runtime",
                "reasoning": "none",
                "kind": "runtime",
                "inputFrom": "Evaluator",
                "outputTo": "User",
            },
        }
    except Exception as e:
        logger.error("Failed to load agents configuration: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load agents configuration") from e

@router.get("/api/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """Get an agent prompt file content."""
    # Validate prompt name to prevent path traversal
    allowed_prompts = ["normalizer", "extracter", "verifier", "generator", "latexifier", "evaluator"]
    if prompt_name not in allowed_prompts:
        raise HTTPException(status_code=404, detail="Prompt not found")

    prompt_path = resolve_prompt_path(PROMPTS_DIR, prompt_name)
    if prompt_path is None:
        raise HTTPException(status_code=404, detail="Prompt file not found")

    try:
        return PlainTextResponse(prompt_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load prompt %s: %s", prompt_name, e)
        raise HTTPException(status_code=500, detail="Failed to load prompt") from e

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
    except Exception as e:
        logger.error("Failed to load evaluation files: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load evaluation files") from e

    return {"files": files}

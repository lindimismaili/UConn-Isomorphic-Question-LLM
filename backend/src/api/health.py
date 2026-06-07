"""Health, OpenAI status, and version endpoints."""

from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

from agents.logger import logger
from fastapi import APIRouter

from backend.src.api.state import (
    OPENAI_STATUS_TIMEOUT_SECONDS,
    OPENAI_STATUS_URL,
    _has_openai_api_key,
)
from backend.src.paths import (
    OUTPUTS_DIR,
    UPLOADS_DIR,
    VERSION_PATHS,
)

router = APIRouter()

@router.get("/api/health")
async def health_check():
    """Health check endpoint for debugging connectivity issues."""
    return {
        "status": "healthy",
        "openai_api_key_configured": _has_openai_api_key(),
        "uploads_dir": str(UPLOADS_DIR),
        "uploads_dir_exists": UPLOADS_DIR.exists(),
        "uploads_dir_writable": os.access(UPLOADS_DIR, os.W_OK),
        "outputs_dir": str(OUTPUTS_DIR),
        "outputs_dir_exists": OUTPUTS_DIR.exists(),
        "outputs_dir_writable": os.access(OUTPUTS_DIR, os.W_OK),
    }

@router.get("/api/openai/status")
async def openai_status():
    """
    Return OpenAI public service status.

    Uses OpenAI's public status page API and marks the site as inactive only
    when indicator is major/critical outage.
    """
    try:
        req = Request(
            OPENAI_STATUS_URL,
            headers={"User-Agent": "IsomorphicQuestionGenerator/1.0"},
        )
        with urlopen(req, timeout=OPENAI_STATUS_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))

        status_payload = payload.get("status", {}) if isinstance(payload, dict) else {}
        indicator = str(status_payload.get("indicator", "unknown")).lower()
        description = str(status_payload.get("description", "Unknown"))
        openai_down = indicator in {"major", "critical"}

        return {
            "status": "ok",
            "openai_down": openai_down,
            "indicator": indicator,
            "description": description,
            "checked_at": datetime.now().isoformat(),
        }
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("OpenAI status check failed: %s", exc)
        return {
            "status": "unknown",
            "openai_down": False,
            "indicator": "unknown",
            "description": "Unable to verify OpenAI status right now.",
            "checked_at": datetime.now().isoformat(),
        }

@router.get("/version.json")
async def get_version_json():
    """
    Return version metadata for the frontend footer.

    In production this is typically written during deploy. In local/dev, return
    a stable fallback object so the UI does not generate 404 noise.
    """
    for version_path in VERSION_PATHS:
        if not version_path.exists():
            continue
        try:
            data = json.loads(version_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {"version": "dev", "published_at": None}

"""Health, OpenAI status, and version endpoints."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter

from backend.src.api.deployment_status import deployment_status_payload
from backend.src.api.state import (
    OPENAI_STATUS_TIMEOUT_SECONDS,
    TERMINAL_STATUSES,
    _has_openai_api_key,
    workflows,
)
from backend.src.operations import fetch_openai_status, operations
from backend.src.paths import (
    OUTPUTS_DIR,
    UPLOADS_DIR,
    VERSION_PATHS,
)

router = APIRouter()


@router.get("/api/deployment/status")
async def deployment_status():
    """Return the sanitized status written by the Pi release updater."""
    active_workflows = sum(1 for workflow in workflows.values() if workflow.get("status") not in TERMINAL_STATUSES)
    return deployment_status_payload(active_workflows=active_workflows)

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


@router.get("/api/service-status")
async def service_status():
    """Public, deliberately minimal availability contract for the web UI."""
    return operations.public_status()


@router.post("/api/visitor/heartbeat")
async def visitor_heartbeat(payload: dict):
    """Record an anonymous, short-lived visitor heartbeat without retaining IPs."""
    operations.record_visitor(str(payload.get("visitorId", "")))
    return {"ok": True}

@router.get("/api/openai/status")
async def openai_status():
    """
    Return OpenAI public service status.

    Uses OpenAI's public status page API and marks the site as inactive only
    when indicator is major/critical outage.
    """
    return fetch_openai_status(OPENAI_STATUS_TIMEOUT_SECONDS)

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

"""Health, OpenAI status, and version endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from agents.logger import logger
from fastapi import APIRouter, Request

from backend.src.api.deployment_status import deployment_status_payload
from backend.src.api.state import (
    OPENAI_STATUS_TIMEOUT_SECONDS,
    OPENAI_STATUS_URL,
    _active_record_or_task_ids,
    _has_openai_api_key,
    batch_jobs,
    batch_tasks,
    eval_batch_jobs,
    eval_batch_tasks,
    workflow_tasks,
    workflows,
)
from backend.src.operations import operations
from backend.src.paths import VERSION_PATHS
from backend.src.safety import safe_exception_category

router = APIRouter()

_DEFAULT_OPENAI_STATUS_CACHE_SECONDS = 60.0
_MAX_OPENAI_STATUS_CACHE_SECONDS = 300.0
_openai_status_cache_lock = threading.Lock()
_openai_status_cache: dict | None = None
_openai_status_cache_expires_at = 0.0


def _openai_status_cache_seconds() -> float:
    """Return a bounded cache duration, falling back safely on bad config."""
    raw = (
        os.getenv(
            "OPENAI_STATUS_CACHE_SECONDS",
            str(_DEFAULT_OPENAI_STATUS_CACHE_SECONDS),
        )
        or ""
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_OPENAI_STATUS_CACHE_SECONDS
    return min(_MAX_OPENAI_STATUS_CACHE_SECONDS, max(5.0, value))


def _fetch_openai_status() -> dict:
    """Fetch the public status payload without running blocking I/O in the loop."""
    request = URLRequest(
        OPENAI_STATUS_URL,
        headers={"User-Agent": "IsomorphicQuestionGenerator/1.0"},
    )
    with urlopen(request, timeout=OPENAI_STATUS_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _fresh_openai_status_result() -> dict:
    """Fetch and sanitize one provider-status result, including failures."""
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        payload = _fetch_openai_status()
        status_payload = payload.get("status", {})
        indicator = str(status_payload.get("indicator", "unknown")).lower()
        description = str(status_payload.get("description", "Unknown"))
        return {
            "status": "ok",
            "openai_down": indicator in {"major", "critical"},
            "indicator": indicator,
            "description": description,
            "checked_at": checked_at,
        }
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "OpenAI status check failed (%s)",
            safe_exception_category(exc),
        )
        return {
            "status": "unknown",
            "openai_down": False,
            "indicator": "unknown",
            "description": "Unable to verify OpenAI status right now.",
            "checked_at": checked_at,
        }


def _cached_openai_status_result() -> dict:
    """Single-flight status lookup with a short success-and-failure cache."""
    global _openai_status_cache, _openai_status_cache_expires_at
    now = time.monotonic()
    with _openai_status_cache_lock:
        if _openai_status_cache is not None and now < _openai_status_cache_expires_at:
            return dict(_openai_status_cache)
        result = _fresh_openai_status_result()
        _openai_status_cache = result
        _openai_status_cache_expires_at = time.monotonic() + _openai_status_cache_seconds()
        return dict(result)


def _reset_openai_status_cache() -> None:
    """Clear status state for deterministic tests."""
    global _openai_status_cache, _openai_status_cache_expires_at
    with _openai_status_cache_lock:
        _openai_status_cache = None
        _openai_status_cache_expires_at = 0.0


@router.get("/api/deployment/status")
async def deployment_status():
    """Return updater status plus every record or task still owning live work."""
    active_workflows = sum(
        len(_active_record_or_task_ids(records, tasks))
        for records, tasks in (
            (workflows, workflow_tasks),
            (batch_jobs, batch_tasks),
            (eval_batch_jobs, eval_batch_tasks),
        )
    )
    return deployment_status_payload(active_workflows=active_workflows)


@router.get("/api/health")
async def health_check():
    """Return public liveness without filesystem or secret-state details."""
    return {"status": "healthy"}


@router.get("/api/readiness")
async def readiness_check(request: Request):
    """Return authenticated readiness details without paths or secret values."""
    return {
        "authenticated": bool(getattr(request.state, "authenticated", False)),
        "openaiApiKeyConfigured": _has_openai_api_key(),
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
    return await asyncio.to_thread(_cached_openai_status_result)

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

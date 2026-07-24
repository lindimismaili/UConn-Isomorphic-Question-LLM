"""Sanitized, persistent deployment status for the local operations dashboard."""

from __future__ import annotations

import json
from typing import Any

from backend.src.paths import RUNTIME_DIR

DEPLOYMENT_STATUS_PATH = RUNTIME_DIR / "deployment_status.json"
_MAX_EVENTS = 50
_MAX_TEXT = 400


def _text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    """Return bounded display text without exposing arbitrary JSON structures."""
    return str(value)[:limit] if isinstance(value, (str, int, float, bool)) else ""


def _event(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "at": _text(value.get("at")),
        "state": _text(value.get("state"), limit=64),
        "stage": _text(value.get("stage"), limit=64),
        "commit": _text(value.get("commit"), limit=64),
        "message": _text(value.get("message")),
    }


def deployment_status_payload(*, active_workflows: int) -> dict[str, Any]:
    """Load the updater state without surfacing filesystem or credential details."""
    fallback = {
        "status": "unavailable",
        "stage": "unknown",
        "active_commit": "",
        "attempted_commit": "",
        "previous_commit": "",
        "rollback_succeeded": None,
        "active_workflows": active_workflows,
        "deferred": False,
        "updated_at": "",
        "events": [],
    }
    try:
        raw = json.loads(DEPLOYMENT_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return fallback
    if not isinstance(raw, dict):
        return fallback

    events = [_event(item) for item in raw.get("events", []) if _event(item) is not None]
    return {
        "status": _text(raw.get("status"), limit=64) or "unknown",
        "stage": _text(raw.get("stage"), limit=64) or "unknown",
        "active_commit": _text(raw.get("active_commit"), limit=64),
        "attempted_commit": _text(raw.get("attempted_commit"), limit=64),
        "previous_commit": _text(raw.get("previous_commit"), limit=64),
        "rollback_succeeded": raw.get("rollback_succeeded") if isinstance(raw.get("rollback_succeeded"), bool) else None,
        "active_workflows": active_workflows,
        "deferred": bool(raw.get("deferred")),
        "updated_at": _text(raw.get("updated_at"), limit=64),
        "events": events[-_MAX_EVENTS:],
    }

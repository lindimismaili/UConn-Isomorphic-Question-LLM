"""Shared offline HTTP-test helpers."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from backend.src.auth import is_cost_bearing_post


class IdempotentTestClient(TestClient):
    """Attach a fresh client key to paid starts unless a test supplies one."""

    def post(self, url: Any, **kwargs: Any):
        path = str(url).split("?", 1)[0]
        if not is_cost_bearing_post("POST", path):
            return super().post(url, **kwargs)
        request_headers = dict(kwargs.pop("headers", {}) or {})
        if "X-Idempotency-Key" not in request_headers:
            request_headers["X-Idempotency-Key"] = str(uuid.uuid4())
        return super().post(url, headers=request_headers, **kwargs)

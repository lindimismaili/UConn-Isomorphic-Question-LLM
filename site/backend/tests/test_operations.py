"""Focused regression coverage for maintenance and operations-state safety."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.src.api import state
from backend.src.api.admin import _safe_path, require_admin
from backend.src.operations import CODE_COST, Operations, classify_openai_failure, operations
from backend.src.server import app


def test_operations_state_is_durable_and_deduplicates_incidents() -> None:
    with TemporaryDirectory() as directory:
        store = Operations(path=Path(directory) / "operations.sqlite3")
        first = store.open_incident(code=CODE_COST, title="Cost", detail="quota")
        second = store.open_incident(code=CODE_COST, title="Cost", detail="quota again")
        assert first["new"] is True
        assert second["new"] is False
        assert second["occurrences"] == 2
        store.set_maintenance(active=True, code=CODE_COST, message="quota")
        assert store.public_status()["maintenance"] is True
        store.set_maintenance(active=False)
        assert store.public_status()["available"] is True


def test_cost_and_auth_failures_are_classified() -> None:
    assert classify_openai_failure("insufficient_quota") is not None
    assert classify_openai_failure("Invalid API key") is not None


def test_admin_api_fails_closed_without_access_configuration() -> None:
    client = TestClient(app)
    response = client.get("/api/admin/session")
    assert response.status_code == 503


def test_admin_dashboard_requires_and_accepts_verified_dependency() -> None:
    client = TestClient(app)
    app.dependency_overrides[require_admin] = lambda: "admin@example.edu"
    try:
        response = client.get("/api/admin/dashboard")
        assert response.status_code == 200
        assert "maintenance" in response.json()
    finally:
        app.dependency_overrides.clear()


def test_admin_file_resolver_rejects_traversal() -> None:
    try:
        _safe_path("outputs", "../../.env")
    except Exception as exc:
        assert getattr(exc, "status_code", None) in {400, 404}
    else:
        raise AssertionError("traversal must be rejected")


def test_maintenance_blocks_uploads_with_public_code() -> None:
    client = TestClient(app)
    operations.set_maintenance(active=True, code=CODE_COST, message="quota")
    try:
        response = client.post("/api/upload", files={"files": ("test.txt", b"test", "text/plain")})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == CODE_COST
    finally:
        operations.set_maintenance(active=False)
        state.workflows.clear()

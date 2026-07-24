"""Focused regression coverage for maintenance and operations-state safety."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from backend.src.api import state
from backend.src.api.admin import _allowed_data_path, _is_real_workflow_id, _safe_path, require_admin
from backend.src.operations import CODE_COST, Operations, classify_openai_failure, operations
from backend.src.server import SPAStaticFiles, app


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


def test_local_admin_mode_requires_opt_in_and_loopback(monkeypatch) -> None:
    from fastapi import Request

    monkeypatch.setenv("ADMIN_LOCAL_DEV_MODE", "true")
    monkeypatch.setenv("ADMIN_LOCAL_DEV_EMAIL", "local-admin@example.test")
    local_request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 49152)})
    remote_request = Request({"type": "http", "headers": [], "client": ("203.0.113.10", 49152)})
    assert require_admin(local_request) == "local-admin@example.test"
    try:
        require_admin(remote_request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("remote requests must not receive local development access")


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


def test_admin_data_allowlist_exposes_only_requested_configuration_files() -> None:
    assert _allowed_data_path(Path("agents.json"))
    assert _allowed_data_path(Path("latex_template.tex"))
    assert _allowed_data_path(Path("rubric.json"))
    assert _allowed_data_path(Path("prompts/generator.txt"))
    assert not _allowed_data_path(Path("evaluation/source.pdf"))
    assert not _allowed_data_path(Path("professor_comparison/Evaluation.xlsx"))
    assert _is_real_workflow_id("495ac582-4933-495c-a2de-c3e25ab2b955")
    assert not _is_real_workflow_id("wf-incomplete")


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


def test_spa_static_files_serves_browser_routes_but_not_api_or_missing_assets() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
        static_app = Starlette(routes=[Mount("/", app=SPAStaticFiles(directory=str(root), html=True))])
        client = TestClient(static_app)
        assert client.get("/admin").status_code == 200
        assert client.get("/api/not-real").status_code == 404
        assert client.get("/assets/missing.js").status_code == 404

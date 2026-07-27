"""Regression coverage for exact local/LAN preview browser origins."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.src.auth import AccessBoundaryConfig
from backend.src.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
PREVIEW_ORIGINS_PATH = REPO_ROOT / "deploy" / "preview_origins.py"


def _load_preview_origins_module():
    spec = importlib.util.spec_from_file_location("preview_origins", PREVIEW_ORIGINS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lan_preview_adds_exact_lan_and_loopback_origins() -> None:
    preview_origins = _load_preview_origins_module()
    config = preview_origins.build_preview_origin_config(
        local_domain="isomorphic.localhost",
        dev_port=5173,
        web_port=8000,
        lan_mode=True,
        extra_origins="https://professor.example",
        lan_hosts=["192.168.50.24"],
    )

    assert config["lanOrigins"] == ["http://192.168.50.24:5173"]
    assert config["allowedOrigins"] == [
        "https://professor.example",
        "http://isomorphic.localhost:5173",
        "http://isomorphic.localhost:8000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "http://192.168.50.24:5173",
    ]
    assert config["allowedOriginsValue"] == ",".join(config["allowedOrigins"])


def test_lan_preview_fails_closed_without_a_reachable_address() -> None:
    preview_origins = _load_preview_origins_module()
    with pytest.raises(ValueError, match="could not determine a non-loopback IPv4 address"):
        preview_origins.build_preview_origin_config(
            local_domain="localhost",
            dev_port=5173,
            web_port=8000,
            lan_mode=True,
            lan_hosts=[],
        )


def test_lan_preview_preserves_or_generates_access_token() -> None:
    preview_origins = _load_preview_origins_module()

    configured = preview_origins.resolve_preview_access_token(
        lan_mode=True,
        configured_token=" professor-configured-token-with-32-characters ",
    )
    generated = preview_origins.resolve_preview_access_token(
        lan_mode=True,
        configured_token="",
    )
    generated_again = preview_origins.resolve_preview_access_token(
        lan_mode=True,
        configured_token=None,
    )
    loopback = preview_origins.resolve_preview_access_token(
        lan_mode=False,
        configured_token="professor-configured-token",
    )

    assert configured == ("professor-configured-token-with-32-characters", False)
    assert generated[1] is True
    assert len(generated[0]) >= 43
    assert generated_again[1] is True
    assert generated_again[0] != generated[0]
    assert loopback == ("", False)


def test_lan_preview_rejects_weak_configured_access_token() -> None:
    preview_origins = _load_preview_origins_module()

    with pytest.raises(ValueError, match="APP_ACCESS_TOKEN"):
        preview_origins.resolve_preview_access_token(
            lan_mode=True,
            configured_token="short-token",
        )


@pytest.mark.parametrize(
    "extra_origin",
    (
        "https://professor.example/path",
        "https://user:password@professor.example",
        "https://professor.example?redirect=https://attacker.example",
        "javascript://professor.example",
    ),
)
def test_preview_rejects_non_origin_allowed_origin_values(extra_origin: str) -> None:
    preview_origins = _load_preview_origins_module()
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS entry"):
        preview_origins.build_preview_origin_config(
            local_domain="localhost",
            dev_port=5173,
            web_port=8000,
            lan_mode=False,
            extra_origins=extra_origin,
        )


def test_generated_lan_origin_allows_unsafe_requests_but_near_matches_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_origins = _load_preview_origins_module()
    config = preview_origins.build_preview_origin_config(
        local_domain="localhost",
        dev_port=5173,
        web_port=8000,
        lan_mode=True,
        lan_hosts=["192.168.50.24"],
    )
    monkeypatch.setenv("ALLOWED_ORIGINS", str(config["allowedOriginsValue"]))
    client = TestClient(
        create_app(
            access_config=AccessBoundaryConfig(
                access_token="",
                rate_limit_requests=10,
                rate_limit_window_seconds=60,
            )
        )
    )

    allowed = client.post(
        "/api/create-data/missing/stop",
        headers={"Origin": "http://192.168.50.24:5173"},
    )
    loopback = client.post(
        "/api/create-data/missing/stop",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    hostile = client.post(
        "/api/create-data/missing/stop",
        headers={"Origin": "https://attacker.example"},
    )
    near_match = client.post(
        "/api/create-data/missing/stop",
        headers={"Origin": "http://192.168.50.24:5173/"},
    )

    assert allowed.status_code == 404
    assert loopback.status_code == 404
    assert hostile.status_code == 403
    assert near_match.status_code == 403


def test_generated_lan_token_enables_public_config_and_protects_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_origins = _load_preview_origins_module()
    token, generated = preview_origins.resolve_preview_access_token(
        lan_mode=True,
        configured_token="",
    )
    monkeypatch.setenv("APP_ACCESS_TOKEN", token)
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://192.168.50.24:5173")
    client = TestClient(create_app())

    public_config = client.get("/api/public-config")
    rejected = client.get("/api/agents")
    accepted = client.get(
        "/api/agents",
        headers={"X-App-Access-Token": token},
    )

    assert generated is True
    assert public_config.status_code == 200
    assert public_config.json()["authRequired"] is True
    assert token not in public_config.text
    assert rejected.status_code == 401
    assert accepted.status_code == 200


@pytest.mark.parametrize("launcher", ("deploy/preview.mjs", "deploy/preview.sh"))
def test_both_preview_launchers_enforce_lan_origin_and_token_contract(launcher: str) -> None:
    script = (REPO_ROOT / launcher).read_text(encoding="utf-8")
    assert "preview_origins.py" in script
    assert "PREVIEW_LAN_HOST" in script
    assert "LAN preview URL:" in script
    assert script.count("One-time Access Gate token:") == 1
    assert "its value is not displayed" in script
    if launcher.endswith(".mjs"):
        assert "backendEnv.APP_ACCESS_TOKEN = originConfig.accessToken" in script
        assert "delete frontendEnv.APP_ACCESS_TOKEN" in script
    else:
        assert 'backend_env+=("APP_ACCESS_TOKEN=${PREVIEW_ACCESS_TOKEN}")' in script
        assert "env -u APP_ACCESS_TOKEN" in script

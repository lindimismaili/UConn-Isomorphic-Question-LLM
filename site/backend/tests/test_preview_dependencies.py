"""Regression coverage for local preview dependency compatibility."""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agents_sdk_requirement_includes_model_refusal_error_release() -> None:
    requirements_source = (REPO_ROOT / "site/backend/requirements.in").read_text().splitlines()
    requirements_lock = (REPO_ROOT / "site/backend/requirements.txt").read_text()

    assert "openai-agents" in requirements_source
    locked_version = re.search(r"^openai-agents==([0-9]+\.[0-9]+\.[0-9]+)", requirements_lock, re.MULTILINE)
    assert locked_version is not None
    assert tuple(int(part) for part in locked_version.group(1).split(".")) >= (0, 15, 0)


def test_preview_dependency_probe_checks_agents_sdk_exception() -> None:
    preview_script = (REPO_ROOT / "deploy/preview.sh").read_text()

    assert "from agents.exceptions import ModelRefusalError" in preview_script


def test_shell_preview_never_kills_a_listener_identified_by_public_health() -> None:
    preview_script = (REPO_ROOT / "deploy/preview.sh").read_text()

    assert "stop_listeners_on_port" not in preview_script
    assert "public_backend_is_healthy" not in preview_script
    assert "kill -9" not in preview_script
    assert "Backend port ${BACKEND_PORT} is in use by another process" in preview_script
    assert "Built web preview port ${WEB_PORT} is in use by another process" in preview_script


def test_preview_scripts_default_to_loopback_and_require_explicit_lan_opt_in() -> None:
    shell_preview = (REPO_ROOT / "deploy/preview.sh").read_text()
    node_preview = (REPO_ROOT / "deploy/preview.mjs").read_text()

    assert 'FRONTEND_HOST="127.0.0.1"' in shell_preview
    assert 'FRONTEND_HOST="0.0.0.0"' in shell_preview
    assert '--host "${FRONTEND_HOST}"' in shell_preview
    assert "[--lan]" in shell_preview

    assert 'frontendHost: lanMode ? "0.0.0.0" : "127.0.0.1"' in node_preview
    assert '"--host", frontendHost' in node_preview
    assert "[--lan]" in node_preview
    assert "BACKEND_HOST must remain loopback-only for preview." in shell_preview
    assert "BACKEND_HOST must remain loopback-only for preview." in node_preview


@pytest.mark.parametrize(
    "command",
    (
        ("bash", "deploy/preview.sh", "--web"),
        ("node", "deploy/preview.mjs", "--web"),
    ),
)
def test_preview_launchers_reject_manual_non_loopback_backend_bind(
    command: tuple[str, ...],
) -> None:
    environment = {**os.environ, "BACKEND_HOST": "0.0.0.0"}
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "BACKEND_HOST must remain loopback-only for preview." in result.stderr


def test_preview_key_probe_honors_backend_environment_precedence() -> None:
    shell_preview = (REPO_ROOT / "deploy/preview.sh").read_text()
    node_preview = (REPO_ROOT / "deploy/preview.mjs").read_text()

    for script in (shell_preview, node_preview):
        assert "from backend.src.bootstrap import load_dotenv" in script
        assert "site/.env.local" in script
    assert "grep -Eq '^OPENAI_API_KEY" not in shell_preview
    assert "readFileSync(envPath" not in node_preview
    assert 'SITE_VENV="${SITE_DIR}/.venv"' in shell_preview


def test_preview_installers_only_mutate_project_local_environments() -> None:
    shell_preview = (REPO_ROOT / "deploy/preview.sh").read_text()
    node_preview = (REPO_ROOT / "deploy/preview.mjs").read_text()

    assert "VIRTUAL_ENV" not in shell_preview
    assert "VIRTUAL_ENV" not in node_preview
    assert "main_py" not in node_preview
    assert '${REPO_DIR}/.venv' not in shell_preview
    assert 'join(repoDir, ".venv")' not in node_preview
    assert 'SITE_VENV="${SITE_DIR}/.venv"' in shell_preview
    assert 'const venvDir = join(siteDir, ".venv")' in node_preview
    for script in (shell_preview, node_preview):
        assert "--require-hashes" in script
        assert ".requirements.sha256" in script
        assert ".package-lock.sha256" in script
        assert "pip" in script and "check" in script


def test_frontend_build_does_not_publish_backend_configuration_assets() -> None:
    vite_config = (REPO_ROOT / "site/frontend/vite.config.js").read_text()
    prompt_viewer = (
        REPO_ROOT / "site/frontend/src/components/modals/PromptViewerModal.jsx"
    ).read_text()
    rubric_viewer = (
        REPO_ROOT / "site/frontend/src/components/modals/RubricViewerModal.jsx"
    ).read_text()

    assert "embedAgentsConfigAsset" not in vite_config
    assert "'/data'" not in vite_config
    assert "/data/prompts/" not in prompt_viewer
    assert "/data/rubric.json" not in rubric_viewer

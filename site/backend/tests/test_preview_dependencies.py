"""Regression coverage for local preview dependency compatibility."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agents_sdk_requirement_includes_model_refusal_error_release() -> None:
    requirements = (REPO_ROOT / "site/backend/requirements.txt").read_text()

    assert "openai-agents>=0.15.0" in requirements.splitlines()


def test_preview_dependency_probe_checks_agents_sdk_exception() -> None:
    preview_script = (REPO_ROOT / "deploy/preview.sh").read_text()

    assert "from agents.exceptions import ModelRefusalError" in preview_script

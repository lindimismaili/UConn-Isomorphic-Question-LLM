"""Validate saved testing fixtures for developer workflows."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from .schemas import AGENT_INPUT_TYPES
except ImportError:
    from schemas import AGENT_INPUT_TYPES


THIS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = THIS_DIR.parent
TESTING_DIR = BACKEND_ROOT / "data" / "testing"
AGENT_INPUTS_DIR = TESTING_DIR / "agent_inputs"
PROMPTS_DIR = TESTING_DIR / "prompts"
SOURCES_DIR = TESTING_DIR / "sources"


def _validate_agent_input_fixtures() -> list[str]:
    """Return validation errors for saved agent-input JSON fixtures."""

    errors: list[str] = []
    for agent_dir in sorted(AGENT_INPUTS_DIR.iterdir() if AGENT_INPUTS_DIR.exists() else []):
        if not agent_dir.is_dir():
            continue
        schema = AGENT_INPUT_TYPES.get(agent_dir.name)
        if schema is None:
            errors.append(f"Unknown agent fixture directory: {agent_dir.name}")
            continue
        for fixture_path in sorted(agent_dir.glob("*.json")):
            try:
                payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"{fixture_path}: invalid JSON ({exc})")
                continue
            try:
                schema.model_validate(payload)
            except Exception as exc:
                errors.append(f"{fixture_path}: schema validation failed ({exc})")
    return errors


def _validate_support_files() -> list[str]:
    """Return validation errors for supporting prompt/source files."""

    errors: list[str] = []
    expected_paths = [
        PROMPTS_DIR / "normalizer_style_prompt.txt",
        SOURCES_DIR / "verbose_open_ended_source.txt",
        SOURCES_DIR / "mixed_question_types_source.txt",
    ]
    for path in expected_paths:
        if not path.exists():
            errors.append(f"Missing support fixture: {path}")
            continue
        if not path.read_text(encoding="utf-8").strip():
            errors.append(f"Support fixture is empty: {path}")
    return errors


def main() -> int:
    """Validate every checked-in testing fixture."""

    errors = _validate_agent_input_fixtures() + _validate_support_files()
    if errors:
        print("Fixture validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("All agent-testing fixtures validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

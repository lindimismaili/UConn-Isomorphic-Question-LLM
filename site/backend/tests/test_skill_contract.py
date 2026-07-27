"""Durable contract checks for repository-local shared skills."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
EXPECTED_SKILLS = {
    "agent-pipeline-changes",
    "check-agents-registry",
    "grader-review",
    "handoff-readiness",
    "run-tests",
    "verify-frontend-ui",
}


def test_shared_skill_packages_are_complete_and_have_stable_unique_ids() -> None:
    skill_directories = {
        path.name: path
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir()
    }
    assert set(skill_directories) == EXPECTED_SKILLS

    ids: set[str] = set()
    for skill_name, skill_dir in sorted(skill_directories.items()):
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert skill_text.startswith("---\n")
        assert re.search(rf"^name:\s*{re.escape(skill_name)}$", skill_text, re.MULTILINE)
        assert re.search(r"^description:\s*\S.+$", skill_text, re.MULTILINE)

        id_match = re.search(r'^id = "([0-9a-f]{32})"$', skill_text, re.MULTILINE)
        assert id_match is not None
        assert id_match.group(1) not in ids
        ids.add(id_match.group(1))

        integration = (skill_dir / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        for required_field in (
            "display_name:",
            "short_description:",
            "default_prompt:",
            'icon_small: "./assets/small-logo.svg"',
            'icon_large: "./assets/large-logo.svg"',
            "allow_implicit_invocation: true",
        ):
            assert required_field in integration

        for icon_name in ("small-logo.svg", "large-logo.svg"):
            icon_path = skill_dir / "assets" / icon_name
            assert icon_path.is_file()
            root = ET.parse(icon_path).getroot()
            assert root.tag.endswith("svg")

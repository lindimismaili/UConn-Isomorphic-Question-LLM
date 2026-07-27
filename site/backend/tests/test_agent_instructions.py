from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.workflow import agents
from backend.src.workflow.agents import _agent_instructions, validate_workflow_assets


class AgentInstructionsTests(unittest.TestCase):
    def test_latexifier_instructions_inject_latex_template(self) -> None:
        instructions = _agent_instructions("latexifier")
        self.assertNotIn("{latex_template}", instructions)
        for macro in ("\\QBlock", "\\OEBlock", "\\MPBlock", "\\MCPart", "\\OEPart", "\\SkipBlock"):
            self.assertIn(macro, instructions)
        self.assertIn("AnsRed", instructions)
        self.assertIn("WrongBlue", instructions)

    def test_other_agent_instructions_load_plain_prompt(self) -> None:
        instructions = _agent_instructions("normalizer")
        self.assertIn("Normalizer Agent", instructions)

    def test_workflow_asset_preflight_accepts_repository_assets(self) -> None:
        validate_workflow_assets()

    def test_invalid_generation_strategy_fails_closed(self) -> None:
        with patch.dict(
            "os.environ",
            {"GENERATION_STRATEGY": "typo"},
        ):
            with self.assertRaisesRegex(ValueError, "GENERATION_STRATEGY"):
                agents.generation_strategy()

    def test_invalid_container_expiry_fails_closed(self) -> None:
        for value in ("not-a-number", "0", "21"):
            with self.subTest(value=value):
                with patch.dict(
                    "os.environ",
                    {"OPENAI_CONTAINER_EXPIRES_MINUTES": value},
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "OPENAI_CONTAINER_EXPIRES_MINUTES",
                    ):
                        agents.container_expires_minutes()

    def test_agents_disable_parallel_tool_calls(self) -> None:
        created = agents.create_agents(
            agent_keys={"normalizer"},
            require_all=True,
        )
        self.assertFalse(
            created["normalizer"].model_settings.parallel_tool_calls
        )

    def test_latex_template_is_required_and_non_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "latex_template.tex"
            with patch.object(agents, "LATEX_TEMPLATE_PATH", template_path):
                with self.assertRaises(FileNotFoundError):
                    agents._load_latex_template()

                template_path.write_text(" \n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "empty"):
                    agents._load_latex_template()

    def test_workflow_asset_preflight_rejects_template_placeholder_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts"
            prompts.mkdir()
            template_path = root / "latex_template.tex"
            template_path.write_text("\\documentclass{article}", encoding="utf-8")
            (prompts / "bootstrap.txt").write_text(
                "Domains: {domains_json}",
                encoding="utf-8",
            )
            (prompts / "bootstrapCompile.txt").write_text(
                "Compile {tex_path} to {basename}",
                encoding="utf-8",
            )

            with (
                patch.object(agents, "PROMPTS_DIR", prompts),
                patch.object(agents, "LATEX_TEMPLATE_PATH", template_path),
                patch.object(agents, "promptable_agents", return_value=[]),
                patch.object(agents, "generation_strategy", return_value="holistic"),
            ):
                with self.assertRaisesRegex(ValueError, r"\{packages\}"):
                    validate_workflow_assets()


if __name__ == "__main__":
    unittest.main()

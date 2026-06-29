from __future__ import annotations

import unittest

from backend.src.workflow.agents import _agent_instructions


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


if __name__ == "__main__":
    unittest.main()

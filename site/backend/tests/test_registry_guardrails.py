"""Guardrail-related registry guarantees.

Pins the invariant that guardrail display nodes (``kind == "guardrail"``) appear
in the full ``agent_sequence()`` in order, but are excluded from every
"runnable"/"LLM"/"stoppable"/start-from view, and that the guardrail config
accessors read ``_config.guardrails``.

Tests against the real ``agents.json`` (no config swap), so no
``_load_raw.cache_clear()`` is needed.
"""

from __future__ import annotations

import unittest

from backend.src import registry


class GuardrailSequenceTests(unittest.TestCase):
    def test_guardrail_nodes_present_in_full_sequence_in_order(self) -> None:
        seq = registry.agent_sequence()
        self.assertIn("input_guardrail", seq)
        self.assertIn("content_guardrail", seq)
        # input_guardrail sits between setup and normalizer; content_guardrail
        # sits between extracter and verifier.
        self.assertLess(seq.index("setup"), seq.index("input_guardrail"))
        self.assertLess(seq.index("input_guardrail"), seq.index("normalizer"))
        self.assertLess(seq.index("extracter"), seq.index("content_guardrail"))
        self.assertLess(seq.index("content_guardrail"), seq.index("verifier"))

    def test_guardrail_nodes_accessor(self) -> None:
        self.assertEqual(
            registry.guardrail_nodes(), ["input_guardrail", "content_guardrail"]
        )

    def test_input_to_agent_sequence_contains_only_model_agents(self) -> None:
        runnable = registry.input_to_agent_sequence()
        self.assertNotIn("input_guardrail", runnable)
        self.assertNotIn("content_guardrail", runnable)
        self.assertNotIn("output", runnable)
        self.assertNotIn("setup", runnable)
        self.assertIn("normalizer", runnable)
        self.assertIn("evaluation", runnable)

    def test_llm_agents_exclude_guardrails(self) -> None:
        llm = registry.llm_agents()
        self.assertNotIn("input_guardrail", llm)
        self.assertNotIn("content_guardrail", llm)

    def test_stoppable_agents_exclude_guardrails_and_runtime(self) -> None:
        stoppable = registry.stoppable_agents()
        for key in ("input_guardrail", "content_guardrail", "setup", "output"):
            self.assertNotIn(key, stoppable)

    def test_source_file_agents_exclude_guardrails(self) -> None:
        source = registry.source_file_agents()
        self.assertNotIn("input_guardrail", source)
        self.assertNotIn("content_guardrail", source)


class GuardrailConfigAccessorTests(unittest.TestCase):
    def test_node_key_accessors_match_registry_keys(self) -> None:
        self.assertEqual(registry.guardrail_input_node(), "input_guardrail")
        self.assertEqual(registry.guardrail_content_node(), "content_guardrail")
        # The node accessors must name real registry keys.
        self.assertIn(registry.guardrail_input_node(), registry.agent_sequence())
        self.assertIn(registry.guardrail_content_node(), registry.agent_sequence())

    def test_config_typed_accessors(self) -> None:
        self.assertIsInstance(registry.guardrails_enabled(), bool)
        self.assertIsInstance(registry.guardrail_block_off_topic(), bool)
        self.assertIsInstance(registry.guardrail_fail_closed(), bool)
        self.assertIsInstance(registry.guardrail_physics_min_confidence(), float)
        self.assertIsInstance(registry.guardrail_max_questions_sampled(), int)
        self.assertTrue(registry.guardrail_moderation_model())
        self.assertTrue(registry.guardrail_model())
        self.assertTrue(registry.guardrail_reasoning())
        self.assertEqual(registry.guardrail_max_output_tokens(), 2048)

    def test_guardrails_enabled_env_override(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"GUARDRAILS_ENABLED": "0"}):
            self.assertFalse(registry.guardrails_enabled())
        with patch.dict(os.environ, {"GUARDRAILS_ENABLED": "yes"}):
            self.assertTrue(registry.guardrails_enabled())
        with patch.dict(os.environ, {"GUARDRAILS_ENABLED": "sometimes"}):
            with self.assertRaises(ValueError):
                registry.guardrails_enabled()

    def test_physics_min_confidence_is_a_probability(self) -> None:
        value = registry.guardrail_physics_min_confidence()
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()

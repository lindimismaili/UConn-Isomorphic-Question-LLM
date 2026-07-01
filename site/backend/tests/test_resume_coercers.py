"""Unit tests for workflow resume/replay input coercers.

Covers the ``coerce_*_from_agent_input`` helpers plus
``extract_agent_input_payload`` and ``agent_input_has_generated_questions``
in ``backend.src.workflow.resume``. Each coercer must accept the several
uploaded-input shapes the frontend can produce: a ``final_result`` container,
a ``result`` container, a bare list, and the agent's own key.

These are pure functions — no app, no network, no filesystem.
"""

from __future__ import annotations

import unittest

from backend.src.schemas import ExtracterOutput
from backend.src.workflow.resume import (
    agent_input_has_generated_questions,
    coerce_extracter_from_agent_input,
    coerce_generator_from_agent_input,
    coerce_latex_from_agent_input,
    coerce_normalizer_from_agent_input,
    coerce_verifier_from_agent_input,
    extract_agent_input_payload,
)


def _options() -> list[dict]:
    """A small set of valid OptionItem dicts."""
    return [
        {"key": key, "text": key, "is_correct": key == "A", "explanation": ""}
        for key in ("A", "B", "C", "D")
    ]


def _source_question(qid: int = 1) -> dict:
    """A minimal valid SourceQuestionItem/VerifierOutput question dict."""
    return {
        "source_id": str(qid),
        "source_raw": f"Question {qid}?",
        "stem": f"Question {qid}?",
        "options": _options(),
        "has_explicit_answer": True,
        "answer": "A",
        "question_type": "multiple_choice",
    }


def _generated_question(qid: int = 1) -> dict:
    """A minimal valid NewQuestionItem (generated/isomorphic) dict."""
    return {
        "id": qid,
        "source_id": str(qid),
        "stem": f"Iso {qid}?",
        "options": _options(),
        "generation_notes": "test",
        "answer": "A",
        "question_type": "multiple_choice",
    }


class NormalizerCoercerTests(unittest.TestCase):
    def test_from_final_result_config(self) -> None:
        payload = {"final_result": {"config": {"title": "Kinematics", "theme": "space"}}}
        out = coerce_normalizer_from_agent_input(payload)
        self.assertEqual(out.title, "Kinematics")
        self.assertEqual(out.theme, "space")

    def test_from_result_config(self) -> None:
        payload = {"result": {"config": {"title": "Optics"}}}
        out = coerce_normalizer_from_agent_input(payload)
        self.assertEqual(out.title, "Optics")

    def test_from_normalizer_config_key(self) -> None:
        payload = {"normalizer_config": {"title": "Waves", "difficulty": "hard"}}
        out = coerce_normalizer_from_agent_input(payload)
        self.assertEqual(out.title, "Waves")
        self.assertEqual(out.difficulty, "hard")

    def test_from_configuration_key(self) -> None:
        payload = {"configuration": {"theme": "ocean"}}
        out = coerce_normalizer_from_agent_input(payload)
        self.assertEqual(out.theme, "ocean")

    def test_from_document_title_and_theme(self) -> None:
        payload = {"document": {"title": "Doc Title", "theme": "Doc Theme"}}
        out = coerce_normalizer_from_agent_input(payload)
        self.assertEqual(out.title, "Doc Title")
        self.assertEqual(out.theme, "Doc Theme")

    def test_empty_payload_returns_defaults(self) -> None:
        out = coerce_normalizer_from_agent_input({})
        # NormalizerOutput() defaults: blank strings, MC count 1.
        self.assertEqual(out.title, "")
        self.assertEqual(out.theme, "")


class ExtracterCoercerTests(unittest.TestCase):
    def test_from_final_result_source_questions(self) -> None:
        payload = {"final_result": {"source_questions": [_source_question(1)]}}
        out = coerce_extracter_from_agent_input(payload)
        self.assertIsInstance(out, ExtracterOutput)
        self.assertEqual(len(out.questions), 1)

    def test_from_result_source_questions(self) -> None:
        payload = {"result": {"source_questions": [_source_question(1), _source_question(2)]}}
        out = coerce_extracter_from_agent_input(payload)
        self.assertEqual(len(out.questions), 2)

    def test_from_top_level_source_questions(self) -> None:
        payload = {"source_questions": [_source_question(1)]}
        out = coerce_extracter_from_agent_input(payload)
        self.assertEqual(len(out.questions), 1)

    def test_from_bare_questions_container(self) -> None:
        payload = {"questions": [_source_question(1)]}
        out = coerce_extracter_from_agent_input(payload)
        self.assertEqual(len(out.questions), 1)

    def test_fills_blank_filename(self) -> None:
        out = coerce_extracter_from_agent_input({"questions": []})
        self.assertEqual(out.filename, "")
        self.assertEqual(out.questions, [])


class VerifierCoercerTests(unittest.TestCase):
    def _extracter(self) -> ExtracterOutput:
        return ExtracterOutput.model_validate(
            {"filename": "", "questions": [_source_question(1), _source_question(2)]}
        )

    def test_from_final_result_verified_questions(self) -> None:
        payload = {"final_result": {"verified_questions": [_source_question(1)]}}
        out = coerce_verifier_from_agent_input(payload, self._extracter())
        self.assertEqual(len(out.questions), 1)

    def test_from_top_level_verified_questions(self) -> None:
        payload = {"verified_questions": [_source_question(1), _source_question(2)]}
        out = coerce_verifier_from_agent_input(payload, self._extracter())
        self.assertEqual(len(out.questions), 2)

    def test_falls_back_to_source_questions_key(self) -> None:
        payload = {"source_questions": [_source_question(9)]}
        out = coerce_verifier_from_agent_input(payload, self._extracter())
        self.assertEqual(len(out.questions), 1)

    def test_falls_back_to_extracter_output_when_absent(self) -> None:
        # No verified/source keys anywhere -> reuse the extracter's questions.
        out = coerce_verifier_from_agent_input({}, self._extracter())
        self.assertEqual(len(out.questions), 2)


class GeneratorCoercerTests(unittest.TestCase):
    def test_from_final_result_generated_questions(self) -> None:
        payload = {"final_result": {"generated_questions": [_generated_question(1)]}}
        out = coerce_generator_from_agent_input(payload)
        self.assertEqual(len(out.isomorphic_questions), 1)

    def test_from_top_level_generated_questions(self) -> None:
        payload = {"generated_questions": [_generated_question(1), _generated_question(2)]}
        out = coerce_generator_from_agent_input(payload)
        self.assertEqual(len(out.isomorphic_questions), 2)

    def test_from_isomorphic_questions_key(self) -> None:
        payload = {"isomorphic_questions": [_generated_question(1)]}
        out = coerce_generator_from_agent_input(payload)
        self.assertEqual(len(out.isomorphic_questions), 1)

    def test_from_bare_questions_list_with_defaults(self) -> None:
        # A bare "questions" list (no generated/isomorphic key) is normalized
        # with default fields filled in per-item.
        payload = {"questions": [{"stem": "Only a stem"}]}
        out = coerce_generator_from_agent_input(payload)
        self.assertEqual(len(out.isomorphic_questions), 1)
        item = out.isomorphic_questions[0]
        self.assertEqual(item.stem, "Only a stem")
        self.assertEqual(item.source_id, "1")
        self.assertIn("uploaded Latexifier input", item.generation_notes)

    def test_bare_questions_skips_non_dict_entries(self) -> None:
        payload = {"questions": ["not a dict", _generated_question(1)]}
        out = coerce_generator_from_agent_input(payload)
        self.assertEqual(len(out.isomorphic_questions), 1)

    def test_empty_payload_yields_no_questions(self) -> None:
        out = coerce_generator_from_agent_input({})
        self.assertEqual(out.isomorphic_questions, [])


class LatexCoercerTests(unittest.TestCase):
    def test_from_latex_output_container(self) -> None:
        payload = {
            "latex_output": {
                "latex_content": "\\documentclass{article}",
                "filename": "questions.tex",
                "pdf_filename": "questions.pdf",
            }
        }
        out = coerce_latex_from_agent_input(payload)
        self.assertEqual(out.latex_content, "\\documentclass{article}")
        self.assertEqual(out.filename, "questions.tex")
        self.assertEqual(out.pdf_filename, "questions.pdf")

    def test_from_latex_alias_container(self) -> None:
        payload = {"latex": {"latex_content": "\\begin{document}", "filename": "a.tex"}}
        out = coerce_latex_from_agent_input(payload)
        self.assertEqual(out.latex_content, "\\begin{document}")
        self.assertEqual(out.filename, "a.tex")

    def test_from_top_level_latex_content_with_defaults(self) -> None:
        payload = {"latex_content": "\\section{x}"}
        out = coerce_latex_from_agent_input(payload)
        self.assertEqual(out.latex_content, "\\section{x}")
        self.assertEqual(out.filename, "isomorphic_questions.tex")
        self.assertEqual(out.pdf_filename, "")

    def test_from_final_result_latex_content(self) -> None:
        payload = {
            "final_result": {
                "latex_content": "\\usepackage{amsmath}",
                "latex_file": "iso.tex",
                "pdf_file": "iso.pdf",
            }
        }
        out = coerce_latex_from_agent_input(payload)
        self.assertEqual(out.latex_content, "\\usepackage{amsmath}")
        self.assertEqual(out.filename, "iso.tex")
        self.assertEqual(out.pdf_filename, "iso.pdf")

    def test_missing_latex_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_latex_from_agent_input({"final_result": {}})

    def test_blank_latex_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            coerce_latex_from_agent_input({"latex_content": "   "})


class ExtractAgentInputPayloadTests(unittest.TestCase):
    def test_returns_structured_dict_directly(self) -> None:
        payload = {"source_questions": [_source_question(1)]}
        self.assertEqual(extract_agent_input_payload(payload), payload)

    def test_unwraps_input_wrapper(self) -> None:
        inner = {"generated_questions": [_generated_question(1)]}
        self.assertEqual(extract_agent_input_payload({"input": inner}), inner)

    def test_parses_json_from_message_input_text(self) -> None:
        # Agents SDK message shape: role/content list with input_text items.
        embedded = {"configuration": {"title": "T"}}
        message = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": '{"configuration": {"title": "T"}}'}],
            }
        ]
        self.assertEqual(extract_agent_input_payload(message), embedded)

    def test_dict_without_structured_keys_returns_empty(self) -> None:
        # A dict with no recognized structured key and no parseable text -> {}.
        self.assertEqual(extract_agent_input_payload({"unrelated": 1}), {})

    def test_non_json_string_returns_empty(self) -> None:
        self.assertEqual(extract_agent_input_payload("not json at all"), {})


class AgentInputHasGeneratedQuestionsTests(unittest.TestCase):
    def test_final_result_with_generated_questions(self) -> None:
        payload = {"final_result": {"generated_questions": [_generated_question(1)]}}
        self.assertTrue(agent_input_has_generated_questions(payload))

    def test_top_level_generated_questions(self) -> None:
        self.assertTrue(
            agent_input_has_generated_questions({"generated_questions": [_generated_question(1)]})
        )

    def test_isomorphic_questions_key(self) -> None:
        self.assertTrue(
            agent_input_has_generated_questions({"isomorphic_questions": [_generated_question(1)]})
        )

    def test_empty_list_is_false(self) -> None:
        self.assertFalse(agent_input_has_generated_questions({"generated_questions": []}))

    def test_missing_is_false(self) -> None:
        self.assertFalse(agent_input_has_generated_questions({"source_questions": [_source_question(1)]}))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from backend.src.schemas import (
    ExtracterOutput,
    GeneratorOutput,
    NewQuestionItem,
    NormalizerOutput,
    SourceQuestionItem,
    VerifierOutput,
)
from backend.src.workflow.payloads import (
    build_extracter_input,
    build_extracter_retry_input,
    build_generator_input,
    build_generator_repair_input,
    build_latex_input,
    build_latex_questions,
    build_normalizer_input,
    build_style_review_input,
    build_verifier_input,
    build_verifier_repair_input,
)


def _source(source_id: str = "q1") -> SourceQuestionItem:
    return SourceQuestionItem(
        source_id=source_id,
        source_raw="raw",
        stem="stem",
        options=[],
        has_explicit_answer=False,
        question_type="open_ended",
        tags=[],
    )


def _generated(source_id: str = "q1") -> NewQuestionItem:
    return NewQuestionItem(
        id=1,
        source_id=source_id,
        stem="stem",
        options=[],
        tags=[],
        generation_notes="notes",
    )


def _config() -> NormalizerOutput:
    return NormalizerOutput(new_questions_per_question=2, difficulty="easy")


class WorkflowPayloadsTests(unittest.TestCase):
    def test_normalizer_input_includes_prompt_and_files(self) -> None:
        payload = build_normalizer_input("make 3 questions", ["a.pdf", "b.pdf"])
        self.assertEqual(payload["task"], "normalize_user_request")
        self.assertEqual(payload["user_input"]["prompt"], "make 3 questions")
        self.assertEqual(payload["user_input"]["files"], ["a.pdf", "b.pdf"])
        self.assertEqual(payload["defaults"]["new_questions_per_question"], 1)
        self.assertEqual(payload["defaults"]["theme"], "")
        self.assertIn("difficulty", payload["rules"])

    def test_normalizer_input_copies_selected_theme_verbatim(self) -> None:
        theme = "Video Games: re-cast each question into a video game scenario."
        payload = build_normalizer_input("make 3 questions", ["a.pdf"], theme)
        self.assertEqual(payload["defaults"]["theme"], theme)
        self.assertIn("verbatim", payload["rules"]["theme"])

    def test_extracter_input_includes_normalizer_config(self) -> None:
        payload = build_extracter_input(["a.pdf"], _config())
        self.assertEqual(payload["task"], "extract_source_questions")
        self.assertEqual(payload["files"], ["a.pdf"])
        self.assertEqual(payload["normalizer_config"]["difficulty"], "easy")
        self.assertTrue(payload["requirements"]["must_include_multiple_choice"])

    def test_extracter_retry_input_has_reason(self) -> None:
        payload = build_extracter_retry_input(["a.pdf"], _config())
        self.assertEqual(payload["task"], "extract_source_questions_retry")
        self.assertEqual(payload["reason"], "previous_result_had_zero_questions")

    def test_verifier_input_carries_extracter_output(self) -> None:
        extraction = ExtracterOutput(questions=[_source("q1")])
        payload = build_verifier_input(extraction)
        self.assertEqual(payload["task"], "verify_source_questions")
        self.assertEqual(len(payload["source_questions"]["questions"]), 1)

    def test_verifier_repair_input_only_carries_failing_questions(self) -> None:
        extraction = ExtracterOutput(questions=[_source("q1"), _source("q2")])
        payload = build_verifier_repair_input(extraction, [_source("q2")], "bad q2")
        self.assertEqual(payload["task"], "verify_source_questions_repair")
        self.assertEqual(payload["reason"], "bad q2")
        question_ids = [q["source_id"] for q in payload["source_questions"]["questions"]]
        self.assertEqual(question_ids, ["q2"])

    def test_generator_input_carries_requirements(self) -> None:
        verifier = VerifierOutput(questions=[_source("q1")])
        payload = build_generator_input(_config(), verifier)
        self.assertEqual(payload["task"], "generate_isomorphic_questions")
        self.assertEqual(payload["requirements"]["new_questions_per_source"], 2)
        self.assertTrue(payload["requirements"]["preserve_source_style_and_verbosity"])

    def test_style_review_input_extends_generator_requirements(self) -> None:
        verifier = VerifierOutput(questions=[_source("q1")])
        generator = GeneratorOutput(isomorphic_questions=[_generated("q1")])
        payload = build_style_review_input(_config(), verifier, generator)
        self.assertEqual(
            payload["task"], "review_and_revise_isomorphic_questions_for_style"
        )
        self.assertTrue(payload["requirements"]["preserve_ids_and_source_ids"])
        self.assertIn("review_focus", payload["requirements"])

    def test_generator_repair_input_only_includes_failing_sources(self) -> None:
        verifier = VerifierOutput(questions=[_source("q1"), _source("q2")])
        generator = GeneratorOutput(
            isomorphic_questions=[_generated("q1"), _generated("q2")]
        )
        payload = build_generator_repair_input(
            _config(), verifier, generator, [_source("q2")], "missing q2 distractor"
        )
        self.assertEqual(payload["task"], "repair_generator_contract_violations")
        question_ids = [q["source_id"] for q in payload["source_questions"]["questions"]]
        self.assertEqual(question_ids, ["q2"])

    def test_latex_input_has_document_metadata(self) -> None:
        extraction = ExtracterOutput(
            questions=[_source("q1")], title="My Quiz", theme="engineering", course="PHYS"
        )
        generator = GeneratorOutput(isomorphic_questions=[_generated("q1")])
        payload = build_latex_input(_config(), extraction, generator)
        self.assertEqual(payload["task"], "format_questions_to_latex")
        self.assertEqual(payload["document"]["source_title"], "My Quiz")
        self.assertEqual(payload["document"]["course"], "PHYS")
        self.assertEqual(
            payload["requirements"]["filename"], "isomorphic_questions.tex"
        )

    def test_latex_questions_carry_answer_and_explanation(self) -> None:
        question = _generated("q1")
        question.answer = "42 m"
        question.explanation = "Apply $d = \\tfrac{1}{2}gt^2$ and substitute."
        generator = GeneratorOutput(isomorphic_questions=[question])
        items = build_latex_questions(generator)
        self.assertEqual(items[0]["answer"], "42 m")
        self.assertEqual(items[0]["explanation"], "Apply $d = \\tfrac{1}{2}gt^2$ and substitute.")


if __name__ == "__main__":
    unittest.main()

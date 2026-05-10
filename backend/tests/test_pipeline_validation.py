from __future__ import annotations

import unittest

from backend.src.pipeline_validation import validate_generator_output, validate_verifier_output
from backend.src.schemas import GeneratorOutput, NewQuestionItem, OptionItem, SourceQuestionItem, VerifierOutput


def option(key: str, text: str, *, correct: bool = False, explanation: str = "because") -> OptionItem:
    return OptionItem(key=key, text=text, is_correct=correct, explanation=explanation)


def source_question(source_id: str = "q1", *, has_figure: bool = False) -> SourceQuestionItem:
    return SourceQuestionItem(
        source_id=source_id,
        source_raw=f"Source {source_id}",
        stem=f"Stem {source_id}",
        options=[option("A", "A", correct=True), option("B", "B")],
        has_explicit_answer=True,
        question_type="multiple_choice",
        topic_tags=[],
        has_figure=has_figure,
        answer="A",
    )


def generated_question(source_id: str = "q1", *, skipped: bool = False) -> NewQuestionItem:
    return NewQuestionItem(
        id=1,
        source_id=source_id,
        stem="" if skipped else f"Generated {source_id}",
        options=[] if skipped else [option("A", "A"), option("B", "B", correct=True)],
        generation_notes="notes",
        question_type="multiple_choice",
        topic_tags=[],
        skipped=skipped,
        skip_reason="figure required" if skipped else "",
    )


class PipelineValidationTests(unittest.TestCase):
    def test_verifier_omission_of_non_figure_source_fails(self) -> None:
        report = validate_verifier_output([source_question("q1")], VerifierOutput(questions=[]))
        self.assertFalse(report.valid)
        self.assertIn("Verifier omitted source_id q1", report.summary())
        self.assertEqual(report.repair_source_ids, ["q1"])

    def test_verifier_omission_of_figure_source_is_allowed(self) -> None:
        report = validate_verifier_output([source_question("q1", has_figure=True)], VerifierOutput(questions=[]))
        self.assertTrue(report.valid)

    def test_generator_requires_exact_count_after_style_review(self) -> None:
        report = validate_generator_output(
            [source_question("q1")],
            GeneratorOutput(isomorphic_questions=[generated_question("q1")]),
            per_source=2,
        )
        self.assertFalse(report.valid)
        self.assertIn("expected 2 item(s), got 1", report.summary())

    def test_generator_figure_placeholder_contract(self) -> None:
        report = validate_generator_output(
            [source_question("fig-1", has_figure=True)],
            GeneratorOutput(isomorphic_questions=[generated_question("fig-1", skipped=True)]),
            per_source=3,
        )
        self.assertTrue(report.valid)

    def test_generator_rejects_incomplete_multiple_choice(self) -> None:
        generated = generated_question("q1")
        generated.options[0].explanation = ""
        report = validate_generator_output([source_question("q1")], GeneratorOutput(isomorphic_questions=[generated]), 1)
        self.assertFalse(report.valid)
        self.assertIn("missing option explanations", report.summary())


if __name__ == "__main__":
    unittest.main()

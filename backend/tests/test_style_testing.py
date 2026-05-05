from __future__ import annotations

import unittest

from backend.src.schemas import NewQuestionItem, SourceQuestionItem
from backend.src.style_testing import (
    extract_agent_input_payload,
    find_style_drift_issues,
    normalize_generated_question_sequence,
)


class StyleTestingTests(unittest.TestCase):
    def test_verbose_open_ended_compression_is_flagged(self) -> None:
        source = SourceQuestionItem(
            source_id="1",
            source_raw=(
                "During a week-long field test for an autonomous hospital delivery cart, "
                "engineers notice that the cart enters the final straight section of the "
                "corridor at \\(4.8\\,\\text{m/s}\\) while a constant braking system can "
                "provide only \\(1.6\\,\\text{m/s}^2\\) of deceleration. The cart is "
                "\\(6.5\\,\\text{m}\\) from a sealed lab door, and the faculty supervisor "
                "wants a written justification rather than a single numerical answer. "
                "Explain whether the cart stops before it reaches the door, and show how "
                "the relevant kinematics relationship supports your conclusion."
            ),
            stem=(
                "During a week-long field test for an autonomous hospital delivery cart, "
                "engineers notice that the cart enters the final straight section of the "
                "corridor at \\(4.8\\,\\text{m/s}\\) while a constant braking system can "
                "provide only \\(1.6\\,\\text{m/s}^2\\) of deceleration. The cart is "
                "\\(6.5\\,\\text{m}\\) from a sealed lab door, and the faculty supervisor "
                "wants a written justification rather than a single numerical answer. "
                "Explain whether the cart stops before it reaches the door, and show how "
                "the relevant kinematics relationship supports your conclusion."
            ),
            options=[],
            has_explicit_answer=False,
            difficulty="",
            question_type="open_ended",
            theme="engineering",
            topic_tags=[],
            has_figure=False,
            answer="",
            parts=[],
        )
        generated = NewQuestionItem(
            id=1,
            source_id="1",
            stem="Will the cart stop before the door? Show the calculation.",
            options=[],
            difficulty="",
            theme="engineering",
            topic_tags=[],
            generation_notes="",
            question_type="open_ended",
            skipped=False,
            skip_reason="",
            answer="No.",
            parts=[],
        )

        issues = find_style_drift_issues([source], [generated])
        self.assertEqual(len(issues), 1)
        self.assertTrue(any("Stem drifted" in message for message in issues[0]["messages"]))

    def test_similar_open_ended_verbosity_is_not_flagged(self) -> None:
        source = SourceQuestionItem(
            source_id="1",
            source_raw="Explain in detail how the cart stops before the door.",
            stem=(
                "During a hallway test, a delivery robot enters the final corridor at "
                "\\(4.5\\,\\text{m/s}\\) and the braking system provides a constant "
                "deceleration of \\(1.5\\,\\text{m/s}^2\\). The supervisor wants a short "
                "written explanation rather than a one-line answer. Explain whether the "
                "robot stops before the door and justify your conclusion."
            ),
            options=[],
            has_explicit_answer=False,
            difficulty="",
            question_type="open_ended",
            theme="engineering",
            topic_tags=[],
            has_figure=False,
            answer="",
            parts=[],
        )
        generated = NewQuestionItem(
            id=1,
            source_id="1",
            stem=(
                "During an evening validation run, a pharmacy robot reaches the last "
                "stretch of corridor at \\(5.0\\,\\text{m/s}\\) while its brakes can supply "
                "only \\(1.7\\,\\text{m/s}^2\\) of constant deceleration. The engineer "
                "reviewing the trial wants a written explanation instead of a bare number. "
                "Explain whether the robot stops before the cabinet and support your answer "
                "with the appropriate kinematics relationship."
            ),
            options=[],
            difficulty="",
            theme="engineering",
            topic_tags=[],
            generation_notes="",
            question_type="open_ended",
            skipped=False,
            skip_reason="",
            answer="No.",
            parts=[],
        )

        issues = find_style_drift_issues([source], [generated])
        self.assertEqual(issues, [])

    def test_generated_questions_are_reordered_and_renumbered(self) -> None:
        source_questions = [
            SourceQuestionItem(
                source_id="1",
                source_raw="Q1",
                stem="Question one.",
                options=[],
                has_explicit_answer=False,
                difficulty="",
                question_type="open_ended",
                theme="engineering",
                topic_tags=[],
                has_figure=False,
                answer="",
                parts=[],
            ),
            SourceQuestionItem(
                source_id="2",
                source_raw="Q2",
                stem="Question two.",
                options=[],
                has_explicit_answer=False,
                difficulty="",
                question_type="open_ended",
                theme="engineering",
                topic_tags=[],
                has_figure=False,
                answer="",
                parts=[],
            ),
        ]
        generated_questions = [
            NewQuestionItem(
                id=7,
                source_id="2",
                stem="Generated two.",
                options=[],
                difficulty="",
                theme="engineering",
                topic_tags=[],
                generation_notes="",
                question_type="open_ended",
                skipped=False,
                skip_reason="",
                answer="",
                parts=[],
            ),
            NewQuestionItem(
                id=4,
                source_id="1",
                stem="Generated one.",
                options=[],
                difficulty="",
                theme="engineering",
                topic_tags=[],
                generation_notes="",
                question_type="open_ended",
                skipped=False,
                skip_reason="",
                answer="",
                parts=[],
            ),
        ]

        ordered = normalize_generated_question_sequence(
            source_questions,
            generated_questions,
        )

        self.assertEqual([question.source_id for question in ordered], ["1", "2"])
        self.assertEqual([question.id for question in ordered], [1, 2])

    def test_extract_agent_input_payload_from_message_format(self) -> None:
        payload = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "{\"task\": \"generate_isomorphic_questions\", \"requirements\": {\"preserve_source_style_and_verbosity\": true}}",
                    }
                ],
            }
        ]
        extracted = extract_agent_input_payload(payload)
        self.assertEqual(extracted["task"], "generate_isomorphic_questions")
        self.assertTrue(extracted["requirements"]["preserve_source_style_and_verbosity"])


if __name__ == "__main__":
    unittest.main()

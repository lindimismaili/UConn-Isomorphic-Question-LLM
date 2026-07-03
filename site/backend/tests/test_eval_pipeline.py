"""Pure unit tests for backend/src/eval_pipeline.py (batch-eval helpers).

No network calls: ``extract_questions_from_pdf`` mocks ``Runner.run`` and
``upload_file_to_openai`` at the module boundary (``eval_pipeline.Runner`` /
``eval_pipeline.upload_file_to_openai``), matching the pattern used in
test_engine_workflow.py. ``create_agents`` runs for real -- it only builds
local ``Agent`` objects from agents.json + prompt files and never touches the
network.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.src import eval_pipeline
from backend.src.schemas import (
    ExtracterOutput,
    NormalizerOutput,
    OptionItem,
    QuestionPart,
    SourceQuestionItem,
)


class ExtractionPayloadTests(unittest.TestCase):
    def test_shape_for_source_pdf(self) -> None:
        config = NormalizerOutput(new_questions_per_question=1, notes="n")
        payload = eval_pipeline._extraction_payload(Path("quiz.pdf"), config, generated_pdf=False)
        self.assertEqual(payload["task"], "extract_source_questions")
        self.assertEqual(payload["files"], ["quiz.pdf"])
        self.assertEqual(payload["normalizer_config"], config.model_dump())
        self.assertIn("source questions", payload["requirements"]["purpose"])
        self.assertTrue(payload["requirements"]["preserve_math_latex"])
        self.assertTrue(payload["requirements"]["do_not_generate_new_questions"])
        self.assertTrue(payload["requirements"]["do_not_rewrite_or_simplify_questions"])
        self.assertEqual(
            payload["requirements"]["multiple_choice_option_keys"], ["A", "B", "C", "D"]
        )

    def test_shape_for_generated_pdf(self) -> None:
        config = NormalizerOutput()
        payload = eval_pipeline._extraction_payload(Path("gen.pdf"), config, generated_pdf=True)
        self.assertEqual(payload["files"], ["gen.pdf"])
        self.assertIn("generated questions", payload["requirements"]["purpose"])


class QuestionToGeneratedItemTests(unittest.TestCase):
    def _question(self, *, source_raw: str) -> SourceQuestionItem:
        return SourceQuestionItem(
            source_id="S1",
            source_raw=source_raw,
            stem="What is 2+2?",
            options=[OptionItem(key="A", text="4", is_correct=True, explanation="e", distractor_logic="")],
            has_explicit_answer=True,
            question_type="multiple_choice",
            answer="A",
            parts=[
                QuestionPart(
                    part_label="a", stem="part a", question_type="multiple_choice", options=[], answer="A"
                )
            ],
        )

    def test_uses_source_raw_when_present(self) -> None:
        q = self._question(source_raw="Raw text of Q1")
        item = eval_pipeline.question_to_generated_item(q, source_id="s1", index=2, pdf_name="doc.pdf")
        self.assertEqual(item["id"], 2)
        self.assertEqual(item["source_id"], "s1")
        self.assertEqual(item["stem"], "Raw text of Q1")
        self.assertEqual(item["options"], [])
        self.assertEqual(item["question_type"], "open_ended")
        self.assertIn("source_raw used", item["generation_notes"])
        self.assertEqual(item["parts"], [])
        self.assertFalse(item["skipped"])
        self.assertEqual(item["answer"], "A")

    def test_falls_back_to_structured_fields_when_no_raw(self) -> None:
        q = self._question(source_raw="   ")
        item = eval_pipeline.question_to_generated_item(q, source_id="s2", index=1, pdf_name="doc.pdf")
        self.assertEqual(item["stem"], "What is 2+2?")
        self.assertEqual(len(item["options"]), 1)
        self.assertEqual(item["options"][0]["key"], "A")
        self.assertEqual(item["question_type"], "multiple_choice")
        self.assertNotIn("source_raw used", item["generation_notes"])
        self.assertEqual(len(item["parts"]), 1)
        self.assertEqual(item["parts"][0]["part_label"], "a")

    def test_falls_back_when_type_unset(self) -> None:
        q = self._question(source_raw="")
        q.question_type = ""
        item = eval_pipeline.question_to_generated_item(q, source_id="s3", index=1, pdf_name="doc.pdf")
        self.assertEqual(item["question_type"], "multiple_choice")


class BuildEvalFinalResultTests(unittest.TestCase):
    def test_structure(self) -> None:
        src_q = SourceQuestionItem(
            source_id="S1", source_raw="raw1", stem="stem1", options=[], has_explicit_answer=True,
            question_type="open_ended", answer="A",
        )
        gen_q = SourceQuestionItem(
            source_id="", source_raw="", stem="genstem", options=[], has_explicit_answer=True,
            question_type="multiple_choice", answer="A",
        )
        source_extraction = ExtracterOutput(
            filename="src.pdf", title="Src Title", theme="physics", questions=[src_q]
        )
        generated_extraction = ExtracterOutput(filename="gen.pdf", questions=[gen_q])

        result = eval_pipeline.build_eval_final_result(
            source_extraction=source_extraction,
            generated_extraction=generated_extraction,
            generated_pdf=Path("gen.pdf"),
        )

        self.assertEqual(result["config"]["theme"], "physics")
        self.assertEqual(result["config"]["title"], "Src Title")
        self.assertEqual(result["config"]["new_questions_per_question"], 1)
        self.assertEqual(len(result["source_questions"]), 1)
        self.assertEqual(len(result["verified_questions"]), 1)
        self.assertEqual(result["source_questions"], result["verified_questions"])
        self.assertEqual(len(result["generated_questions"]), 1)
        self.assertEqual(result["generated_questions"][0]["source_id"], "S1")
        self.assertEqual(result["generated_questions"][0]["id"], 1)

    def test_title_falls_back_when_source_title_blank(self) -> None:
        source_extraction = ExtracterOutput(filename="src.pdf", questions=[])
        generated_extraction = ExtracterOutput(filename="gen.pdf", questions=[])
        result = eval_pipeline.build_eval_final_result(
            source_extraction=source_extraction,
            generated_extraction=generated_extraction,
            generated_pdf=Path("gen.pdf"),
        )
        self.assertEqual(result["config"]["title"], "Evaluation Run")
        self.assertEqual(result["generated_questions"], [])


class ExtractQuestionsFromPdfTests(unittest.TestCase):
    def test_returns_extraction_and_normalizes_mc_type(self) -> None:
        q = SourceQuestionItem(
            source_id="S1", source_raw="", stem="stem",
            options=[OptionItem(key="A", text="x", is_correct=True, explanation="", distractor_logic="")],
            has_explicit_answer=True, question_type="", answer="A",
        )
        extraction = ExtracterOutput(filename="", title="T", theme="physics", questions=[q])

        async def _run():
            with (
                patch.object(eval_pipeline, "upload_file_to_openai", return_value="file_123"),
                patch.object(eval_pipeline, "Runner") as runner,
            ):
                runner.run = AsyncMock(return_value=SimpleNamespace(final_output=extraction))
                result = await eval_pipeline.extract_questions_from_pdf(
                    client=MagicMock(),
                    pdf_path=Path("quiz.pdf"),
                    run_config=MagicMock(),
                    generated_pdf=False,
                )
                self.assertEqual(runner.run.await_count, 1)
                return result

        result = asyncio.run(_run())
        self.assertEqual(result.filename, "quiz.pdf")
        self.assertEqual(result.questions[0].question_type, "multiple_choice")

    def test_retries_when_first_result_has_zero_questions(self) -> None:
        empty = ExtracterOutput(filename="", questions=[])
        non_empty = ExtracterOutput(
            filename="",
            questions=[
                SourceQuestionItem(
                    source_id="S1", source_raw="", stem="s", options=[], has_explicit_answer=False,
                    question_type="open_ended", answer="",
                )
            ],
        )
        call_count = {"n": 0}

        async def fake_run(_agent, *, input, run_config):  # noqa: A002 - matches Runner.run's kwarg name
            call_count["n"] += 1
            return SimpleNamespace(final_output=empty if call_count["n"] == 1 else non_empty)

        async def _run():
            with (
                patch.object(eval_pipeline, "upload_file_to_openai", return_value="file_123"),
                patch.object(eval_pipeline, "Runner") as runner,
            ):
                runner.run = AsyncMock(side_effect=fake_run)
                return await eval_pipeline.extract_questions_from_pdf(
                    client=MagicMock(), pdf_path=Path("quiz.pdf"), run_config=MagicMock(), generated_pdf=True,
                )

        result = asyncio.run(_run())
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(len(result.questions), 1)


if __name__ == "__main__":
    unittest.main()

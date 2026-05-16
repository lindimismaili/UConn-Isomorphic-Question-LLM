from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.src.evaluation import (
    EVALUATION_DATASET_FILENAME,
    EVALUATION_ITEMS_FILENAME,
    EVALUATION_OUTPUTS_DIRNAME,
    _attach_report_file_metadata,
    _build_testing_criteria,
    _render_generated_question_text,
    _resolve_latex_check,
    run_openai_output_evaluation,
)


class FailingFiles:
    def create(self, **_: object) -> object:
        raise RuntimeError("stop after dataset write")


class FailingEvalClient:
    files = FailingFiles()
    evals = object()


class EvaluationContractTests(unittest.TestCase):
    def test_rendering_includes_correctness_explanations_and_answers(self) -> None:
        rendered = _render_generated_question_text(
            {
                "stem": "What happens?",
                "question_type": "multiple_choice",
                "answer": "B",
                "options": [
                    {"key": "A", "text": "Wrong", "is_correct": False, "explanation": "sign error"},
                    {"key": "B", "text": "Right", "is_correct": True, "explanation": "uses Newton's law"},
                ],
            }
        )
        self.assertIn("A. Wrong [incorrect] Explanation: sign error", rendered)
        self.assertIn("B. Right [correct] Explanation: uses Newton's law", rendered)
        self.assertIn("Answer: B", rendered)

        multipart = _render_generated_question_text(
            {
                "stem": "Answer both.",
                "question_type": "multi_part",
                "parts": [
                    {"part_label": "a", "stem": "Find speed.", "question_type": "open_ended", "answer": "3 m/s"},
                    {
                        "part_label": "b",
                        "stem": "Choose force.",
                        "question_type": "multiple_choice",
                        "answer": "A",
                        "options": [
                            {"key": "A", "text": "10 N", "is_correct": True, "explanation": "F=ma"},
                        ],
                    },
                ],
            }
        )
        self.assertIn("Part a: Find speed.", multipart)
        self.assertIn("Part answer: 3 m/s", multipart)
        self.assertIn("A. 10 N [correct] Explanation: F=ma", multipart)

    def test_latex_check_uses_explicit_pdf_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            latex_path = root / "custom.tex"
            pdf_path = root / "renamed.pdf"
            latex_path.write_text("\\documentclass{article}", encoding="utf-8")
            pdf_path.write_bytes(b"%PDF-1.4")
            check = _resolve_latex_check(latex_path, pdf_path)
        self.assertEqual(check["status"], "passed")
        self.assertEqual(check["pdf_file"], "renamed.pdf")

    def test_figure_skip_placeholders_are_excluded_from_score_model_dataset(self) -> None:
        final_result = {
            "verified_questions": [
                {
                    "source_id": "fig",
                    "source_raw": "Figure question",
                    "stem": "Figure question",
                    "question_type": "multiple_choice",
                    "has_figure": True,
                    "options": [],
                },
                {
                    "source_id": "q1",
                    "source_raw": "Normal question",
                    "stem": "Normal question",
                    "question_type": "open_ended",
                    "has_figure": False,
                    "options": [],
                    "answer": "42",
                },
            ],
            "generated_questions": [
                {
                    "id": 1,
                    "source_id": "fig",
                    "stem": "",
                    "question_type": "multiple_choice",
                    "options": [],
                    "parts": [],
                    "skipped": True,
                    "skip_reason": "Requires a figure",
                },
                {
                    "id": 2,
                    "source_id": "q1",
                    "stem": "Generated normal question",
                    "question_type": "open_ended",
                    "options": [],
                    "parts": [],
                    "answer": "43",
                    "skipped": False,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = run_openai_output_evaluation(
                FailingEvalClient(),
                final_result,
                root,
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )
            dataset_path = root / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_DATASET_FILENAME
            rows = [json.loads(line)["item"] for line in dataset_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report["deterministic_checks"]["figure_skips"]["status"], "passed")
        self.assertEqual([row["source_id"] for row in rows], ["q1"])

    def test_evaluation_agent_outputs_filename_is_reported_in_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evaluation_dir = root / EVALUATION_OUTPUTS_DIRNAME
            evaluation_dir.mkdir()
            report_path = evaluation_dir / "evaluation_report.json"
            dataset_path = evaluation_dir / EVALUATION_DATASET_FILENAME
            output_items_path = evaluation_dir / EVALUATION_ITEMS_FILENAME
            spreadsheet_path = evaluation_dir / "evaluation_spreadsheet.csv"

            for path in (report_path, dataset_path, output_items_path, spreadsheet_path):
                path.write_text("", encoding="utf-8")

            report = _attach_report_file_metadata(
                {},
                report_path=report_path,
                dataset_path=dataset_path,
                output_items_path=output_items_path,
                spreadsheet_path=spreadsheet_path,
                rubric_file="evaluation_outputs/rubric.json",
                source_files=[],
            )

        self.assertEqual(EVALUATION_ITEMS_FILENAME, "evaluation_agents.jsonl")
        self.assertIn(
            {
                "name": "evaluation_outputs/evaluation_agents.jsonl",
                "label": "Evaluation agent outputs",
                "kind": "items",
            },
            report["testing_files"],
        )

    def test_score_model_eval_uses_low_reasoning_effort(self) -> None:
        criteria = _build_testing_criteria(model="test-model")

        self.assertGreater(len(criteria), 0)
        for criterion in criteria:
            self.assertEqual(
                criterion.get("sampling_params", {}).get("reasoning_effort"),
                "low",
            )

    def test_score_model_uses_pass_fail_range(self) -> None:
        criteria = _build_testing_criteria(model="test-model")
        self.assertGreater(len(criteria), 0)
        na_criteria = {"numerical_variation", "numeric_reasonableness"}
        for criterion in criteria:
            self.assertEqual(criterion.get("pass_threshold"), 1.0)
            if criterion["name"] in na_criteria:
                self.assertEqual(criterion.get("range"), [-1, 1])
            else:
                self.assertEqual(criterion.get("range"), [0, 1])

    def test_workflow_and_grading_models_use_gpt_5_5(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        agents = json.loads((repo_root / "backend/data/agents.json").read_text(encoding="utf-8"))
        rubric = json.loads((repo_root / "backend/data/rubric.json").read_text(encoding="utf-8"))

        self.assertGreater(len(agents), 0)
        for config in agents.values():
            self.assertEqual(config.get("model"), "gpt-5.5")

        self.assertEqual(rubric.get("model"), "gpt-5.5")
        for criterion in _build_testing_criteria():
            self.assertEqual(criterion.get("model"), "gpt-5.5")


if __name__ == "__main__":
    unittest.main()

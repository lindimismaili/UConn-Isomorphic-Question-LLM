from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from backend.src import evaluation
from backend.src.evaluation import (
    EVALUATION_DATASET_FILENAME,
    EVALUATION_ITEMS_FILENAME,
    EVALUATION_OUTPUTS_DIRNAME,
    GRADER_SEED,
    _attach_report_file_metadata,
    _build_testing_criteria,
    _render_generated_question_text,
    rubric_reasoning_effort,
    run_openai_output_evaluation,
)
from backend.src.schemas import NormalizerOutput


class FailingFiles:
    def create(self, **_: object) -> object:
        raise RuntimeError("stop after dataset write")


class FailingEvalClient:
    files = FailingFiles()
    evals = object()


class _FakeFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.created: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        return SimpleNamespace(id="file_eval")

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)


class _FakeOutputItem:
    def __init__(self, item: dict, results: list[SimpleNamespace]) -> None:
        self.datasource_item = item
        self.results = results

    def model_dump(self, *args, **kwargs) -> dict:
        return {
            "datasource_item": self.datasource_item,
            "results": [
                {
                    "name": result.name,
                    "score": result.score,
                }
                for result in self.results
            ],
        }


class _FakeOutputPages:
    def __init__(self, output_items: list[_FakeOutputItem]) -> None:
        self.output_items = output_items

    def iter_pages(self):
        yield SimpleNamespace(data=self.output_items)


class _FakeOutputItems:
    def __init__(self, output_items: list[_FakeOutputItem]) -> None:
        self.output_items = output_items

    def list(self, **_: object) -> _FakeOutputPages:
        return _FakeOutputPages(self.output_items)


class _FakeRuns:
    def __init__(self, *, status: str, output_items: list[_FakeOutputItem]) -> None:
        self.status = status
        self.output_items = _FakeOutputItems(output_items)
        self.created = 0
        self.cancelled: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def create(self, **_: object) -> object:
        self.created += 1
        return SimpleNamespace(id="run_eval")

    def retrieve(self, **_: object) -> object:
        return SimpleNamespace(id="run_eval", status=self.status)

    def cancel(self, *, eval_id: str, run_id: str) -> None:
        self.cancelled.append((eval_id, run_id))

    def delete(self, run_id: str, *, eval_id: str) -> None:
        self.deleted.append((eval_id, run_id))


class _FakeEvals:
    def __init__(self, *, status: str, output_items: list[_FakeOutputItem]) -> None:
        self.runs = _FakeRuns(status=status, output_items=output_items)
        self.created = 0
        self.deleted: list[str] = []

    def create(self, **_: object) -> object:
        self.created += 1
        return SimpleNamespace(id="eval_test")

    def delete(self, eval_id: str) -> None:
        self.deleted.append(eval_id)


class _FakeEvalClient:
    def __init__(self, *, status: str, output_items: list[_FakeOutputItem]) -> None:
        self.files = _FakeFiles()
        self.evals = _FakeEvals(status=status, output_items=output_items)


def _question_pair(source_id: str, *, generated_stem: str) -> tuple[dict, dict]:
    source = {
        "source_id": source_id,
        "source_raw": f"Original {source_id}",
        "stem": f"Source {source_id}",
        "question_type": "multiple_choice",
        "answer": "B",
        "group_context": "Use the shared setup.",
        "options": [
            {
                "key": "A",
                "text": "wrong",
                "is_correct": False,
                "explanation": "sign error",
                "distractor_logic": "sign_error",
            },
            {
                "key": "B",
                "text": "right",
                "is_correct": True,
                "explanation": "correct",
                "distractor_logic": "",
            },
        ],
    }
    generated = {
        "id": 1,
        "source_id": source_id,
        "stem": generated_stem,
        "question_type": "open_ended",
        "answer": "42",
        "options": [],
        "parts": [],
        "skipped": False,
    }
    return source, generated


class EvaluationContractTests(unittest.TestCase):
    def test_normalizer_question_count_is_bounded(self) -> None:
        self.assertEqual(NormalizerOutput(new_questions_per_question=1).new_questions_per_question, 1)
        self.assertEqual(NormalizerOutput(new_questions_per_question=5).new_questions_per_question, 5)
        for value in (0, -1, 6, 1_000_000):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                NormalizerOutput(new_questions_per_question=value)

    def test_evaluation_item_limit_fails_before_remote_eval_creation(self) -> None:
        source_one, generated_one = _question_pair("S1", generated_stem="Generated one")
        source_two, generated_two = _question_pair("S2", generated_stem="Generated two")
        client = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(evaluation, "max_evaluation_items", return_value=1):
                report = run_openai_output_evaluation(
                    client,
                    {
                        "verified_questions": [source_one, source_two],
                        "generated_questions": [generated_one, generated_two],
                    },
                    Path(tmp),
                )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["items_submitted"], 2)
        self.assertIn("MAX_EVALUATION_ITEMS=1", report["reason"])
        client.files.create.assert_not_called()
        client.evals.create.assert_not_called()

    def test_evaluation_upload_has_crash_safe_provider_expiry(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        output = _FakeOutputItem(
            {
                "source_id": "S1",
                "source_question_text": "Source",
                "generated_question_text": "Generated",
            },
            [],
        )
        client = _FakeEvalClient(status="completed", output_items=[output])

        with tempfile.TemporaryDirectory() as tmp:
            run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(tmp),
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )

        self.assertEqual(client.files.created[0]["purpose"], "evals")
        self.assertEqual(
            client.files.created[0]["expires_after"],
            {"anchor": "created_at", "seconds": 86400},
        )

    def test_evaluation_csv_escapes_formula_like_untrusted_text(self) -> None:
        dataset_item = {
            "source_id": "=HYPERLINK(\"https://attacker.example\")",
            "source_question_text": " \t+SUM(1,1)",
            "generated_question_text": "@malicious",
            "source_distractor_logic": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            spreadsheet = Path(tmp) / "evaluation.csv"
            self.assertTrue(
                evaluation._write_evaluation_spreadsheet(
                    spreadsheet,
                    [dataset_item],
                    [],
                    1.0,
                )
            )
            with spreadsheet.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))

        self.assertTrue(row["source_id"].startswith("'="))
        self.assertTrue(row["source_question_text"].startswith("'"))
        self.assertTrue(row["generated_question_text"].startswith("'@"))

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

    def test_rendering_includes_group_shared_context(self) -> None:
        rendered = _render_generated_question_text(
            {
                "stem": "What is the maximum measurable energy?",
                "question_type": "multiple_choice",
                "answer": "B",
                "group_id": "G1",
                "group_context": "For questions 1-3, consider a particle in a box.",
                "options": [
                    {"key": "A", "text": "x", "is_correct": False, "explanation": "no"},
                    {"key": "B", "text": "y", "is_correct": True, "explanation": "yes"},
                ],
            }
        )
        self.assertIn("Shared context: For questions 1-3, consider a particle in a box.", rendered)
        self.assertIn("What is the maximum measurable energy?", rendered)

    def test_source_rendering_keeps_raw_and_verified_structure(self) -> None:
        source, _ = _question_pair("S1", generated_stem="Generated")
        rendered = evaluation._render_source_question_text(source)
        self.assertIn("Original source text:\nOriginal S1", rendered)
        self.assertIn("Verified structured record:", rendered)
        self.assertIn("Shared context: Use the shared setup.", rendered)
        self.assertIn("A. wrong [incorrect] Explanation: sign error", rendered)
        self.assertIn("Answer: B", rendered)
        self.assertEqual(
            evaluation._summarize_source_distractor_logic(source),
            "option A: sign_error",
        )

    def test_partial_eval_is_incomplete_and_csv_keeps_every_submission(self) -> None:
        source_one, generated_one = _question_pair("S1", generated_stem="Generated one")
        source_two, generated_two = _question_pair("S2", generated_stem="Generated two")
        output_item_data = {
            "source_id": "S1",
            "source_question_text": evaluation._render_source_question_text(source_one),
            "generated_question_text": evaluation._render_generated_question_text(generated_one),
            "source_distractor_logic": evaluation._summarize_source_distractor_logic(source_one),
        }
        results = [
            SimpleNamespace(name=criterion["name"], score=1.0)
            for criterion in evaluation.question_level_criteria()
        ]
        client = _FakeEvalClient(
            status="completed",
            output_items=[_FakeOutputItem(output_item_data, results)],
        )
        final_result = {
            "verified_questions": [source_one, source_two],
            "generated_questions": [generated_one, generated_two],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = run_openai_output_evaluation(
                client,
                final_result,
                root,
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )
            spreadsheet = (
                root
                / EVALUATION_OUTPUTS_DIRNAME
                / "evaluation_spreadsheet.csv"
            )
            with spreadsheet.open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["items_submitted"], 2)
        self.assertEqual(report["items_received"], 1)
        self.assertEqual(report["items_scored"], 1)
        self.assertEqual(report["items_evaluated"], 1)
        self.assertEqual(len(csv_rows), 2)
        self.assertEqual(client.files.deleted, ["file_eval"])
        self.assertEqual(client.evals.runs.deleted, [("eval_test", "run_eval")])
        self.assertEqual(client.evals.deleted, ["eval_test"])

    def test_missing_criterion_and_unknown_source_force_incomplete(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        unknown = {**generated, "id": 2, "source_id": "UNKNOWN"}
        item = {
            "source_id": "S1",
            "source_question_text": evaluation._render_source_question_text(source),
            "generated_question_text": evaluation._render_generated_question_text(generated),
            "source_distractor_logic": evaluation._summarize_source_distractor_logic(source),
        }
        criteria = evaluation.question_level_criteria()
        results = [
            SimpleNamespace(name=criterion["name"], score=1.0)
            for criterion in criteria[:-1]
        ]
        client = _FakeEvalClient(
            status="completed",
            output_items=[_FakeOutputItem(item, results)],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated, unknown],
                },
                Path(temp_dir),
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["items_submitted"], 1)
        self.assertEqual(report["items_received"], 1)
        self.assertEqual(report["items_scored"], 0)
        self.assertEqual(
            report["deterministic_checks"]["evaluation_input_coverage"]["unknown_source_ids"],
            ["UNKNOWN"],
        )
        self.assertIn("criterion_coverage_mismatch", report["deterministic_checks"])

    def test_american_canceled_status_is_terminal(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        client = _FakeEvalClient(status="canceled", output_items=[])
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(temp_dir),
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )

        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["run_status"], "canceled")
        self.assertEqual(report["items_evaluated"], 0)

    def test_cooperative_cancellation_stops_polling_and_cancels_remote_run(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        client = _FakeEvalClient(status="running", output_items=[])
        cancellation_checks = iter((False, False, False, True))
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(temp_dir),
                grader_model="test-model",
                poll_interval_seconds=10,
                max_wait_seconds=900,
                should_cancel=lambda: next(cancellation_checks),
            )

        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["run_status"], "cancelled")
        self.assertEqual(
            client.evals.runs.cancelled,
            [("eval_test", "run_eval")],
        )

    def test_immediate_cancellation_creates_no_remote_resources(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        client = _FakeEvalClient(status="running", output_items=[])
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(temp_dir),
                grader_model="test-model",
                should_cancel=lambda: True,
            )

        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(client.files.created, [])
        self.assertEqual(client.evals.created, 0)
        self.assertEqual(client.evals.runs.created, 0)
        self.assertEqual(client.evals.runs.cancelled, [])

    def test_timeout_cancels_remote_run_and_reports_incomplete(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        client = _FakeEvalClient(status="running", output_items=[])
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(temp_dir),
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertIn("did not reach a terminal state", report["reason"])
        self.assertEqual(
            client.evals.runs.cancelled,
            [("eval_test", "run_eval")],
        )

    def test_poll_exception_attempts_cancel_without_masking_primary_error(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        client = _FakeEvalClient(status="running", output_items=[])
        poll_secret = "EVAL_POLL_SECRET_dfc6"
        cancel_secret = "EVAL_CANCEL_SECRET_4b19"
        client.evals.runs.retrieve = MagicMock(
            side_effect=RuntimeError(poll_secret)
        )
        client.evals.runs.cancel = MagicMock(
            side_effect=RuntimeError(cancel_secret)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertLogs(evaluation.logger.name, level="WARNING") as captured:
                report = run_openai_output_evaluation(
                    client,
                    {
                        "verified_questions": [source],
                        "generated_questions": [generated],
                    },
                    Path(temp_dir),
                    grader_model="test-model",
                    poll_interval_seconds=0,
                    max_wait_seconds=0,
                )
            persisted_report = json.loads(
                (
                    Path(temp_dir)
                    / evaluation.EVALUATION_OUTPUTS_DIRNAME
                    / evaluation.EVALUATION_REPORT_FILENAME
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "Evaluation run failed (RuntimeError)")
        self.assertEqual(report["error_type"], "RuntimeError")
        serialized = json.dumps(persisted_report)
        logs = "\n".join(captured.output)
        self.assertNotIn(poll_secret, serialized)
        self.assertNotIn(cancel_secret, serialized)
        self.assertNotIn(poll_secret, logs)
        self.assertNotIn(cancel_secret, logs)
        client.evals.runs.cancel.assert_called_once_with(
            eval_id="eval_test",
            run_id="run_eval",
        )

    def test_remote_cleanup_failures_do_not_mask_completed_report(self) -> None:
        source, generated = _question_pair("S1", generated_stem="Generated")
        item = {
            "source_id": "S1",
            "source_question_text": evaluation._render_source_question_text(source),
            "generated_question_text": evaluation._render_generated_question_text(generated),
            "source_distractor_logic": evaluation._summarize_source_distractor_logic(source),
        }
        results = [
            SimpleNamespace(name=criterion["name"], score=1.0)
            for criterion in evaluation.question_level_criteria()
        ]
        client = _FakeEvalClient(
            status="completed",
            output_items=[_FakeOutputItem(item, results)],
        )
        client.evals.runs.delete = MagicMock(side_effect=RuntimeError("run cleanup failed"))
        client.evals.delete = MagicMock(side_effect=RuntimeError("eval cleanup failed"))

        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_openai_output_evaluation(
                client,
                {
                    "verified_questions": [source],
                    "generated_questions": [generated],
                },
                Path(temp_dir),
                grader_model="test-model",
                poll_interval_seconds=0,
                max_wait_seconds=0,
            )

        self.assertEqual(report["status"], "complete")
        client.evals.runs.delete.assert_called_once_with(
            "run_eval",
            eval_id="eval_test",
        )
        client.evals.delete.assert_called_once_with("eval_test")
        self.assertEqual(client.files.deleted, ["file_eval"])

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

    def test_score_model_eval_uses_per_criterion_reasoning_effort(self) -> None:
        # Reasoning effort is now resolved per criterion (its own rubric ``reasoning``
        # field, falling back to the global default), not a single global value.
        with patch.dict(
            "os.environ",
            {"GRADER_MAX_COMPLETION_TOKENS": "4096"},
        ):
            criteria = _build_testing_criteria(model="test-model")

        self.assertGreater(len(criteria), 0)
        by_name = {criterion["name"]: criterion for criterion in criteria}
        # Hard criteria are raised above the global default.
        self.assertEqual(
            by_name["physics_correctness"]["sampling_params"]["reasoning_effort"],
            "high",
        )
        self.assertEqual(
            by_name["distractor_structure_preservation"]["sampling_params"]["reasoning_effort"],
            "high",
        )
        # Unset criteria inherit the global rubric default.
        global_effort = rubric_reasoning_effort()
        self.assertEqual(
            by_name["scenario_variation"]["sampling_params"]["reasoning_effort"],
            global_effort,
        )
        # Every grader is seeded for reproducibility.
        for criterion in criteria:
            self.assertEqual(criterion["sampling_params"].get("seed"), GRADER_SEED)
            self.assertEqual(
                criterion["sampling_params"].get("max_completions_tokens"),
                4096,
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

    def test_workflow_and_grading_models_use_configured_gpt_5_6(self) -> None:
        # Model policy: the generator runs on the high-reasoning "terra" tier; every
        # other LLM stage and the grader run on the cheaper "luna" tier. Only these
        # two model ids may appear across the pipeline.
        repo_root = Path(__file__).resolve().parents[2]
        agents = json.loads((repo_root / "backend/data/agents.json").read_text(encoding="utf-8"))
        rubric = json.loads((repo_root / "backend/data/rubric.json").read_text(encoding="utf-8"))

        allowed_models = {"gpt-5.6-terra", "gpt-5.6-luna"}

        llm_agents = {k: v for k, v in agents.items() if isinstance(v, dict) and v.get("isLLM") and v.get("model") is not None}
        self.assertGreater(len(llm_agents), 0)
        for key, config in llm_agents.items():
            self.assertIn(config.get("model"), allowed_models, msg=f"{key} model")
        # The generator is the only stage on the terra tier; everything else is luna.
        self.assertEqual(agents["generator"].get("model"), "gpt-5.6-terra")
        for key, config in llm_agents.items():
            if key != "generator":
                self.assertEqual(config.get("model"), "gpt-5.6-luna", msg=f"{key} model")

        self.assertEqual(rubric.get("model"), "gpt-5.6-luna")
        for criterion in _build_testing_criteria():
            self.assertEqual(criterion.get("model"), "gpt-5.6-luna")


if __name__ == "__main__":
    unittest.main()

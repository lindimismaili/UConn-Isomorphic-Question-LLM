"""Offline tests for the batch-evaluation routes (backend/src/api/eval_batch.py).

Covers both ``mode=prior_runs`` (evaluating existing output folders) and
``mode=upload`` (evaluating an uploaded source + generated PDF pair), plus the
outputs listing and status/result/stop routes. Everything is offline:
``get_openai_client`` and ``run_eval_batch_async`` are patched at the module
boundary they're imported into, and any filesystem writes happen under a
``tempfile.TemporaryDirectory`` patched in for ``OUTPUTS_DIR``/``UPLOADS_DIR``
(never the real backend directories).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.api import eval_batch as eval_batch_module
from backend.src.api import state
from backend.src.server import app
from backend.tests.support import IdempotentTestClient as TestClient


class EvalBatchRouteTestBase(unittest.TestCase):
    """Shared client + registry reset for eval-batch route tests."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._saved = dict(state.eval_batch_jobs)
        self._saved_create_batches = dict(state.batch_jobs)
        state.eval_batch_jobs.clear()
        state.batch_jobs.clear()

    def tearDown(self) -> None:
        state.eval_batch_jobs.clear()
        state.eval_batch_jobs.update(self._saved)
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_create_batches)


class StartEvalBatchPriorRunsTests(EvalBatchRouteTestBase):
    def test_requires_explicit_projected_grader_call_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "run-ok"
            run_dir.mkdir()
            (run_dir / "final_result.json").write_text(
                '{"generated_questions":[{"source_id":"S1"}]}',
                encoding="utf-8",
            )
            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "OUTPUTS_DIR", outputs_dir),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "prior_runs", "folders": '["run-ok"]'},
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("projected grader calls", response.text)
        self.assertEqual(state.eval_batch_jobs, {})

    def test_rejects_aggregate_items_before_scheduling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            for name in ("one", "two"):
                run_dir = outputs_dir / name
                run_dir.mkdir()
                (run_dir / "final_result.json").write_text(
                    '{"generated_questions":[{"source_id":"S1"},{"source_id":"S2"}]}',
                    encoding="utf-8",
                )
            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "OUTPUTS_DIR", outputs_dir),
                patch.object(eval_batch_module, "MAX_EVAL_BATCH_ITEMS", 3),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={
                        "mode": "prior_runs",
                        "folders": '["one","two"]',
                        "confirm_grader_calls": "true",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("MAX_EVAL_BATCH_ITEMS=3", response.text)
        self.assertEqual(state.eval_batch_jobs, {})

    def test_rejects_second_active_batch_across_batch_types(self) -> None:
        state.batch_jobs["active-create-data"] = {"status": "running"}
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "run-ok"
            run_dir.mkdir()
            (run_dir / "final_result.json").write_text(
                '{"generated_questions":[{"source_id":"S1"}]}',
                encoding="utf-8",
            )
            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "OUTPUTS_DIR", outputs_dir),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={
                        "mode": "prior_runs",
                        "folders": '["run-ok"]',
                        "confirm_grader_calls": "true",
                    },
                )

        self.assertEqual(response.status_code, 429)

    def test_404_when_selected_run_has_no_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            (outputs_dir / "run-no-result").mkdir()
            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "OUTPUTS_DIR", outputs_dir),
            ):
                resp = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "prior_runs", "folders": json.dumps(["run-no-result"])},
                )
        self.assertEqual(resp.status_code, 404)

    def test_success_creates_entries_from_prior_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "run-ok"
            run_dir.mkdir()
            (run_dir / "final_result.json").write_text(
                '{"generated_questions":[{"source_id":"S1"}]}',
                encoding="utf-8",
            )

            async def _noop(_batch_id: str) -> None:
                return None

            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "OUTPUTS_DIR", outputs_dir),
                patch.object(eval_batch_module, "run_eval_batch_async", new=_noop),
            ):
                resp = self.client.post(
                    "/api/eval-batch/start",
                    data={
                        "mode": "prior_runs",
                        "folders": json.dumps(["run-ok"]),
                        "confirm_grader_calls": "true",
                    },
                )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["mode"], "prior_runs")
        self.assertEqual(body["totalFiles"], 1)
        self.assertEqual(body["projectedExtractionFiles"], 0)
        self.assertEqual(body["projectedExtractionCalls"], 0)
        self.assertEqual(body["projectedBootstrapCalls"], 0)
        status = self.client.get(
            f"/api/eval-batch/{body['batchId']}/status"
        ).json()
        self.assertEqual(status["projectedExtractionFiles"], 0)
        self.assertEqual(status["projectedExtractionCalls"], 0)
        self.assertEqual(status["projectedBootstrapCalls"], 0)

        batch = state.eval_batch_jobs[body["batchId"]]
        self.assertEqual(batch["mode"], "prior_runs")
        self.assertEqual(len(batch["entries"]), 1)
        self.assertEqual(batch["entries"][0]["folder"], "run-ok")
        self.assertEqual(batch["entries"][0]["status"], "queued")


class StartEvalBatchUploadTests(EvalBatchRouteTestBase):
    def test_cost_confirmation_discloses_isolated_extraction_and_bootstrap_calls(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(
                    eval_batch_module,
                    "get_openai_client",
                    return_value=object(),
                ),
                patch.object(eval_batch_module, "UPLOADS_DIR", Path(tmp)),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "upload"},
                    files=[
                        (
                            "source_file",
                            ("source.pdf", b"%PDF-1.4 src", "application/pdf"),
                        ),
                        (
                            "generated_files",
                            ("gen1.pdf", b"%PDF-1.4 g1", "application/pdf"),
                        ),
                        (
                            "generated_files",
                            ("gen2.pdf", b"%PDF-1.4 g2", "application/pdf"),
                        ),
                    ],
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("isolates 3 PDF(s)", response.text)
        self.assertIn("3 document-dependency bootstrap call(s)", response.text)
        self.assertIn("up to 6 Extracter agent call(s)", response.text)
        self.assertIn(
            "Safety moderation and content-classifier checks also run",
            response.text,
        )

    def test_413_when_source_file_too_large(self) -> None:
        with (
            patch.object(eval_batch_module, "get_openai_client", return_value=object()),
            patch.object(eval_batch_module, "MAX_UPLOAD_BYTES", 10),
        ):
            resp = self.client.post(
                "/api/eval-batch/start",
                data={"mode": "upload", "confirm_grader_calls": "true"},
                files=[
                    ("source_file", ("source.pdf", b"%PDF-1.4 this source is too big", "application/pdf")),
                    ("generated_files", ("gen.pdf", b"%PDF-1.4 gen", "application/pdf")),
                ],
            )
        self.assertEqual(resp.status_code, 413)

    def test_success_creates_entries_from_uploaded_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = Path(tmp)

            async def _noop(_batch_id: str) -> None:
                return None

            with (
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "UPLOADS_DIR", uploads_dir),
                patch.object(eval_batch_module, "run_eval_batch_async", new=_noop),
            ):
                resp = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "upload", "confirm_grader_calls": "true"},
                    files=[
                        ("source_file", ("source.pdf", b"%PDF-1.4 src", "application/pdf")),
                        ("generated_files", ("gen1.pdf", b"%PDF-1.4 g1", "application/pdf")),
                        ("generated_files", ("gen2.pdf", b"%PDF-1.4 g2", "application/pdf")),
                    ],
                )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["mode"], "upload")
        self.assertEqual(body["totalFiles"], 2)
        self.assertEqual(body["projectedExtractionFiles"], 3)
        self.assertEqual(body["projectedExtractionCalls"], 6)
        self.assertEqual(body["projectedBootstrapCalls"], 3)
        status = self.client.get(
            f"/api/eval-batch/{body['batchId']}/status"
        ).json()
        self.assertEqual(status["projectedExtractionFiles"], 3)
        self.assertEqual(status["projectedExtractionCalls"], 6)
        self.assertEqual(status["projectedBootstrapCalls"], 3)

        batch = state.eval_batch_jobs[body["batchId"]]
        self.assertEqual(batch["source_name"], "source.pdf")
        self.assertEqual(batch["uploadFolder"], f"eval_batch_{body['batchId']}")
        self.assertEqual(batch["projectedExtractionFiles"], 3)
        self.assertEqual(batch["projectedExtractionCalls"], 6)
        self.assertEqual(batch["projectedBootstrapCalls"], 3)
        names = {e["name"] for e in batch["entries"]}
        self.assertEqual(names, {"gen1.pdf", "gen2.pdf"})
        for entry in batch["entries"]:
            self.assertEqual(entry["status"], "queued")


class ListEvalOutputsTests(EvalBatchRouteTestBase):
    def test_returns_runs_from_list_output_runs(self) -> None:
        fake_runs = [
            {
                "folder": "r1", "label": "r1", "mtime": 1.0,
                "questionCount": 3, "hasEval": True, "existingScore": 0.9,
            }
        ]
        with patch.object(eval_batch_module, "_list_output_runs", return_value=fake_runs):
            resp = self.client.get("/api/eval-batch/outputs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["runs"], fake_runs)
        self.assertEqual(body["total"], 1)
        self.assertEqual(
            body["policy"]["maxUploadExtractionFiles"],
            eval_batch_module.MAX_EVAL_SELECTIONS + 1,
        )
        self.assertEqual(
            body["policy"]["maxUploadExtractionCalls"],
            (eval_batch_module.MAX_EVAL_SELECTIONS + 1) * 2,
        )
        self.assertEqual(
            body["policy"]["maxUploadBootstrapCalls"],
            eval_batch_module.MAX_EVAL_SELECTIONS + 1,
        )


class EvalBatchStatusResultStopTests(EvalBatchRouteTestBase):
    def test_status_unknown_batch_is_404(self) -> None:
        resp = self.client.get("/api/eval-batch/does-not-exist/status")
        self.assertEqual(resp.status_code, 404)

    def test_result_unknown_batch_is_404(self) -> None:
        resp = self.client.get("/api/eval-batch/does-not-exist/result")
        self.assertEqual(resp.status_code, 404)

    def test_stop_unknown_batch_is_404(self) -> None:
        resp = self.client.post("/api/eval-batch/does-not-exist/stop")
        self.assertEqual(resp.status_code, 404)

    def test_result_400_when_not_terminal(self) -> None:
        batch_id = "eb-running"
        state.eval_batch_jobs[batch_id] = {
            "status": "running", "mode": "prior_runs", "totalFiles": 1, "completedFiles": 0,
            "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 0, "outputFolder": "",
            "entries": [], "error": "", "created_at": "x", "updated_at": "x",
        }
        resp = self.client.get(f"/api/eval-batch/{batch_id}/result")
        self.assertEqual(resp.status_code, 400)

    def test_stop_cancels_non_terminal_entries(self) -> None:
        batch_id = "eb-stop"
        entries = [
            {"name": "a.pdf", "folder": "a", "status": "queued", "score": None,
             "itemsEvaluated": 0, "outputFolder": "", "error": ""},
            {"name": "b.pdf", "folder": "b", "status": "completed", "score": 0.8,
             "itemsEvaluated": 4, "outputFolder": "out/b", "error": ""},
        ]
        state.eval_batch_jobs[batch_id] = {
            "status": "running", "mode": "prior_runs", "totalFiles": 2, "completedFiles": 1,
            "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 1, "outputFolder": "",
            "entries": entries, "error": "", "created_at": "x", "updated_at": "x",
        }
        resp = self.client.post(f"/api/eval-batch/{batch_id}/stop")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "cancelled")
        updated = {e["name"]: e for e in state.eval_batch_jobs[batch_id]["entries"]}
        self.assertEqual(updated["a.pdf"]["status"], "cancelled")
        self.assertEqual(updated["a.pdf"]["error"], "Stopped by user")
        self.assertEqual(updated["b.pdf"]["status"], "completed")
        self.assertEqual(updated["b.pdf"]["error"], "")


if __name__ == "__main__":
    unittest.main()

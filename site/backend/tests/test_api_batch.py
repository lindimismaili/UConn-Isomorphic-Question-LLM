"""Offline tests for the create-data batch machinery.

Covers the FastAPI routes in ``backend/src/api/batch.py`` (start/stop/status/
result) plus a lower-level concurrency test that drives
``backend.src.api.state.run_create_data_batch_async`` directly to verify the
per-batch semaphore actually bounds concurrent entry execution. Everything
here is offline: ``get_openai_client`` and the batch-runner coroutines are
patched at the module boundary they're imported into, and any filesystem
writes happen under a ``tempfile.TemporaryDirectory`` (never the real
``backend/outputs`` or ``backend/data/evaluation``).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.src.api import batch as batch_module
from backend.src.api import state
from backend.src.server import app


class BatchRouteTestBase(unittest.TestCase):
    """Shared client + registry reset for create-data batch route tests."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._saved_batches = dict(state.batch_jobs)
        self._saved_workflows = dict(state.workflows)
        state.batch_jobs.clear()
        state.workflows.clear()

    def tearDown(self) -> None:
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_batches)
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)


class StartCreateDataTests(BatchRouteTestBase):
    def test_returns_503_when_openai_client_unavailable(self) -> None:
        with patch.object(batch_module, "get_openai_client", side_effect=RuntimeError("no key")):
            resp = self.client.post("/api/create-data/start")
        self.assertEqual(resp.status_code, 503)

    def test_start_success_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_dir = Path(tmp)
            f1 = eval_dir / "quiz_one.pdf"
            f1.write_bytes(b"%PDF-1.4 x")
            (eval_dir / "sub").mkdir()
            f2 = eval_dir / "sub" / "quiz_two.pdf"
            f2.write_bytes(b"%PDF-1.4 y")
            fake_files = [f1, f2]

            async def _noop(_batch_id: str) -> None:
                return None

            with (
                patch.object(batch_module, "get_openai_client", return_value=object()),
                patch.object(batch_module, "EVALUATION_DIR", eval_dir),
                patch.object(batch_module, "_list_evaluation_source_files", return_value=fake_files),
                patch.object(batch_module, "run_create_data_batch_async", new=_noop),
            ):
                resp = self.client.post("/api/create-data/start", data={"prompt": "make it harder"})

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("batchId", body)
        self.assertEqual(body["totalFiles"], 2)

        batch_id = body["batchId"]
        self.assertIn(batch_id, state.batch_jobs)
        entries = state.batch_jobs[batch_id]["entries"]
        self.assertEqual(len(entries), 2)
        for entry in entries:
            for key in (
                "name",
                "relativePath",
                "folderName",
                "status",
                "workflowId",
                "outputFolder",
                "error",
            ):
                self.assertIn(key, entry)
            self.assertEqual(entry["status"], "queued")
            self.assertEqual(entry["workflowId"], "")
            self.assertEqual(entry["outputFolder"], "")
            self.assertEqual(entry["error"], "")

    def test_404_when_no_evaluation_files(self) -> None:
        with (
            patch.object(batch_module, "get_openai_client", return_value=object()),
            patch.object(batch_module, "_list_evaluation_source_files", return_value=[]),
        ):
            resp = self.client.post("/api/create-data/start")
        self.assertEqual(resp.status_code, 404)


class StopCreateDataTests(BatchRouteTestBase):
    def test_stop_cancels_non_terminal_and_preserves_terminal_entries(self) -> None:
        batch_id = "stop-batch"
        entries = [
            {
                "name": "a.pdf", "relativePath": "a.pdf", "folderName": "a",
                "status": "queued", "workflowId": "", "outputFolder": "", "error": "",
            },
            {
                "name": "b.pdf", "relativePath": "b.pdf", "folderName": "b",
                "status": "running", "workflowId": "", "outputFolder": "", "error": "",
            },
            {
                "name": "c.pdf", "relativePath": "c.pdf", "folderName": "c",
                "status": "completed", "workflowId": "", "outputFolder": "out/c", "error": "",
            },
            {
                "name": "d.pdf", "relativePath": "d.pdf", "folderName": "d",
                "status": "errored", "workflowId": "", "outputFolder": "", "error": "boom",
            },
        ]
        state.batch_jobs[batch_id] = {
            "status": "running",
            "prompt": "",
            "totalFiles": 4,
            "completedFiles": 1,
            "failedFiles": 1,
            "cancelledFiles": 0,
            "doneFiles": 2,
            "outputFolder": "out",
            "entries": entries,
            "summaryFile": "",
            "error": "",
            "created_at": "x",
            "updated_at": "x",
        }

        resp = self.client.post(f"/api/create-data/{batch_id}/stop")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "cancelled")

        updated = {e["name"]: e for e in state.batch_jobs[batch_id]["entries"]}
        self.assertEqual(updated["a.pdf"]["status"], "cancelled")
        self.assertEqual(updated["a.pdf"]["error"], "Stopped by user")
        self.assertEqual(updated["b.pdf"]["status"], "cancelled")
        self.assertEqual(updated["b.pdf"]["error"], "Stopped by user")
        # Terminal entries are untouched.
        self.assertEqual(updated["c.pdf"]["status"], "completed")
        self.assertEqual(updated["c.pdf"]["error"], "")
        self.assertEqual(updated["d.pdf"]["status"], "errored")
        self.assertEqual(updated["d.pdf"]["error"], "boom")

    def test_stop_unknown_batch_is_404(self) -> None:
        resp = self.client.post("/api/create-data/does-not-exist/stop")
        self.assertEqual(resp.status_code, 404)


class StatusResultCreateDataTests(BatchRouteTestBase):
    def test_status_unknown_batch_is_404(self) -> None:
        resp = self.client.get("/api/create-data/does-not-exist/status")
        self.assertEqual(resp.status_code, 404)

    def test_result_unknown_batch_is_404(self) -> None:
        resp = self.client.get("/api/create-data/does-not-exist/result")
        self.assertEqual(resp.status_code, 404)

    def test_result_400_when_not_terminal(self) -> None:
        batch_id = "running-batch"
        state.batch_jobs[batch_id] = {
            "status": "running", "prompt": "", "totalFiles": 1, "completedFiles": 0,
            "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 0, "outputFolder": "",
            "entries": [], "summaryFile": "", "error": "", "created_at": "x", "updated_at": "x",
        }
        resp = self.client.get(f"/api/create-data/{batch_id}/result")
        self.assertEqual(resp.status_code, 400)

    def test_status_and_result_success_shape(self) -> None:
        batch_id = "done-batch"
        state.batch_jobs[batch_id] = {
            "status": "completed", "prompt": "", "totalFiles": 1, "completedFiles": 1,
            "failedFiles": 0, "cancelledFiles": 0, "doneFiles": 1, "outputFolder": "out",
            "entries": [], "summaryFile": "batch_summary.json", "error": "",
            "created_at": "x", "updated_at": "x",
        }
        status_resp = self.client.get(f"/api/create-data/{batch_id}/status")
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["status"], "completed")

        result_resp = self.client.get(f"/api/create-data/{batch_id}/result")
        self.assertEqual(result_resp.status_code, 200)
        body = result_resp.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["summaryFile"], "batch_summary.json")


class BatchConcurrencyTests(unittest.TestCase):
    """Drives ``state.run_create_data_batch_async`` directly to verify the
    ``BATCH_WORKFLOW_CONCURRENCY`` semaphore actually bounds how many entries
    run at once."""

    def setUp(self) -> None:
        self._saved_batches = dict(state.batch_jobs)
        self._saved_workflows = dict(state.workflows)
        state.batch_jobs.clear()
        state.workflows.clear()

    def tearDown(self) -> None:
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_batches)
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)

    def test_semaphore_bounds_concurrent_entry_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs_dir = root / "outputs"
            eval_dir = root / "evaluation"
            outputs_dir.mkdir()
            eval_dir.mkdir()

            entries = []
            for i in range(6):
                fpath = eval_dir / f"file{i}.pdf"
                fpath.write_bytes(b"%PDF-1.4 x")
                entries.append(
                    {
                        "name": fpath.name,
                        "relativePath": fpath.name,
                        "folderName": f"file{i}",
                        "status": "queued",
                        "workflowId": "",
                        "outputFolder": "",
                        "error": "",
                    }
                )

            batch_id = "concurrency-batch"
            state.batch_jobs[batch_id] = {
                "status": "starting",
                "prompt": "",
                "totalFiles": len(entries),
                "completedFiles": 0,
                "failedFiles": 0,
                "cancelledFiles": 0,
                "doneFiles": 0,
                "outputFolder": "",
                "entries": entries,
                "summaryFile": "",
                "error": "",
                "created_at": "x",
                "updated_at": "x",
            }

            current = 0
            max_seen = 0

            async def fake_execute_workflow(*_args, **_kwargs) -> None:
                nonlocal current, max_seen
                current += 1
                max_seen = max(max_seen, current)
                await asyncio.sleep(0.02)
                current -= 1

            with (
                patch.object(state, "OUTPUTS_DIR", outputs_dir),
                patch.object(state, "EVALUATION_DIR", eval_dir),
                patch.object(state, "BATCH_WORKFLOW_CONCURRENCY", 2),
                patch.object(state, "_execute_workflow", fake_execute_workflow),
                patch.object(state, "_persist_workflow"),
                patch.object(state, "_persist_batch"),
            ):
                asyncio.run(state.run_create_data_batch_async(batch_id))

            self.assertEqual(max_seen, 2)
            batch = state.batch_jobs[batch_id]
            self.assertEqual(batch["status"], "completed")
            self.assertEqual(batch["completedFiles"], 6)
            self.assertEqual(batch["failedFiles"], 0)


if __name__ == "__main__":
    unittest.main()

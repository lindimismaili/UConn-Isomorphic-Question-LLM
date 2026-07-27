"""FastAPI route tests using ``TestClient`` — offline and deterministic.

These exercise the HTTP surface without touching OpenAI. Endpoints that call
``get_openai_client()`` are either avoided or patched. Shared in-memory
registries (``state.workflows`` / ``state.batch_jobs``) are reset around each
test so nothing leaks between cases.

Covered:
- Read-only config routes: health, agents (incl. guardrail + runtime nodes),
  dropdown, rubric, prompts (valid + 404), evaluation files.
- Upload hardening happy/error paths via ``POST /api/upload``.
- Download path-traversal guards + unknown-workflow 404s.
- Workflow status/result shaping for injected records (incl. ``blocked``).
"""

from __future__ import annotations

import asyncio
import time
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from fastapi.testclient import TestClient

from backend.src import registry
from backend.src.api import files as files_module
from backend.src.api import health, state
from backend.src.server import app

# A tiny valid single-page PDF (starts with the %PDF magic bytes).
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


class RouteTestBase(unittest.TestCase):
    """Shared client + registry reset for route tests."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self._saved_workflows = dict(state.workflows)
        self._saved_workflow_tasks = dict(state.workflow_tasks)
        self._saved_batches = dict(state.batch_jobs)
        self._saved_batch_tasks = dict(state.batch_tasks)
        self._saved_eval_batches = dict(state.eval_batch_jobs)
        self._saved_eval_batch_tasks = dict(state.eval_batch_tasks)
        self._saved_workflow_reservations = dict(
            state.workflow_admission_reservations
        )
        self._saved_workflow_owners = dict(state.workflow_admission_owners)
        state.workflows.clear()
        state.workflow_tasks.clear()
        state.batch_jobs.clear()
        state.batch_tasks.clear()
        state.eval_batch_jobs.clear()
        state.eval_batch_tasks.clear()
        state.workflow_admission_reservations.clear()
        state.workflow_admission_owners.clear()

    def tearDown(self) -> None:
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)
        state.workflow_tasks.clear()
        state.workflow_tasks.update(self._saved_workflow_tasks)
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_batches)
        state.batch_tasks.clear()
        state.batch_tasks.update(self._saved_batch_tasks)
        state.eval_batch_jobs.clear()
        state.eval_batch_jobs.update(self._saved_eval_batches)
        state.eval_batch_tasks.clear()
        state.eval_batch_tasks.update(self._saved_eval_batch_tasks)
        state.workflow_admission_reservations.clear()
        state.workflow_admission_reservations.update(
            self._saved_workflow_reservations
        )
        state.workflow_admission_owners.clear()
        state.workflow_admission_owners.update(self._saved_workflow_owners)


class HealthAndVersionRouteTests(RouteTestBase):
    def setUp(self) -> None:
        super().setUp()
        health._reset_openai_status_cache()

    def test_health_ok(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "healthy"})

    def test_openai_status_caches_success_and_failure_results(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"status":{"indicator":"none","description":"All Systems Operational"}}'
        )
        with patch("backend.src.api.health.urlopen", return_value=response) as urlopen:
            first = self.client.get("/api/openai/status")
            second = self.client.get("/api/openai/status")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json(), first.json())
        urlopen.assert_called_once()

        health._reset_openai_status_cache()
        with patch(
            "backend.src.api.health.urlopen",
            side_effect=URLError("offline"),
        ) as urlopen:
            first = self.client.get("/api/openai/status")
            second = self.client.get("/api/openai/status")
        self.assertEqual(first.json()["status"], "unknown")
        self.assertEqual(second.json(), first.json())
        urlopen.assert_called_once()

    def test_version_json_returns_object(self) -> None:
        resp = self.client.get("/version.json")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), dict)
        self.assertIn("version", resp.json())

    def test_deployment_status_is_sanitized(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "deployment_status.json"
            path.write_text(
                '{"status":"rolled_back","stage":"health","active_commit":"good","secret":"nope",'
                '"events":[{"at":"now","state":"failed","stage":"build","commit":"bad",'
                '"message":"safe","environment":"hidden"}]}',
                encoding="utf-8",
            )
            with patch("backend.src.api.deployment_status.DEPLOYMENT_STATUS_PATH", path):
                response = self.client.get("/api/deployment/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rolled_back")
        self.assertEqual(response.json()["events"][0]["message"], "safe")
        self.assertNotIn("secret", response.json())
        self.assertNotIn("environment", response.json()["events"][0])

    def test_deployment_status_handles_missing_or_invalid_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "deployment_status.json"
            with patch("backend.src.api.deployment_status.DEPLOYMENT_STATUS_PATH", path):
                self.assertEqual(self.client.get("/api/deployment/status").json()["status"], "unavailable")
                path.write_text("not json", encoding="utf-8")
                self.assertEqual(self.client.get("/api/deployment/status").json()["status"], "unavailable")

    def test_deployment_status_counts_every_active_job_registry(self) -> None:
        state.workflows["workflow-running"] = {"status": "running"}
        state.workflows["workflow-done"] = {"status": "completed"}
        state.batch_jobs["batch-running"] = {"status": "running"}
        state.eval_batch_jobs["eval-running"] = {"status": "pending"}
        response = self.client.get("/api/deployment/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_workflows"], 3)

    def test_deployment_status_counts_cancelled_jobs_until_tasks_finish(self) -> None:
        unfinished_tasks = [MagicMock(), MagicMock(), MagicMock()]
        for task in unfinished_tasks:
            task.done.return_value = False

        state.workflows["workflow-cancelled"] = {"status": "cancelled"}
        state.workflow_tasks["workflow-cancelled"] = unfinished_tasks[0]
        state.batch_jobs["batch-cancelled"] = {"status": "cancelled"}
        state.batch_tasks["batch-cancelled"] = unfinished_tasks[1]
        state.eval_batch_jobs["eval-cancelled"] = {"status": "cancelled"}
        state.eval_batch_tasks["eval-cancelled"] = unfinished_tasks[2]

        response = self.client.get("/api/deployment/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_workflows"], 3)

    def test_deployment_status_deduplicates_records_and_ignores_done_tasks(self) -> None:
        unfinished_task = MagicMock()
        unfinished_task.done.return_value = False
        finished_task = MagicMock()
        finished_task.done.return_value = True

        state.workflows["workflow-running"] = {"status": "running"}
        state.workflow_tasks["workflow-running"] = unfinished_task
        state.batch_tasks["batch-finished"] = finished_task

        response = self.client.get("/api/deployment/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_workflows"], 1)


class ArchiveBuildTests(RouteTestBase):
    def test_concurrent_zip_requests_share_one_background_build(self) -> None:
        calls = 0

        def fake_build(_output_dir: Path, zip_path: Path) -> None:
            nonlocal calls
            calls += 1
            time.sleep(0.02)
            zip_path.write_bytes(b"zip")

        async def drive(output_dir: Path, zip_path: Path) -> None:
            await asyncio.gather(
                files_module._build_zip_single_flight("workflow", output_dir, zip_path),
                files_module._build_zip_single_flight("workflow", output_dir, zip_path),
            )

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            zip_path = output_dir / "workflow.zip"
            with patch.object(files_module, "_build_workflow_zip", new=fake_build):
                asyncio.run(drive(output_dir, zip_path))

        self.assertEqual(calls, 1)

    def test_zip_builder_skips_symlinks_and_enforces_source_size(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir = root / "run"
            output_dir.mkdir()
            (output_dir / "result.txt").write_text("safe", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            (output_dir / "linked.txt").symlink_to(outside)
            zip_path = output_dir / "run.zip"

            state._build_workflow_zip(output_dir, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["result.txt"])
            self.assertTrue(
                state._workflow_zip_is_current(output_dir, zip_path)
            )
            (output_dir / "later.txt").write_text("new", encoding="utf-8")
            self.assertFalse(
                state._workflow_zip_is_current(output_dir, zip_path)
            )

            zip_path.unlink()
            with (
                patch.object(state, "download_zip_max_source_bytes", return_value=1),
                self.assertRaisesRegex(ValueError, "too large"),
            ):
                state._build_workflow_zip(output_dir, zip_path)


class ConfigRouteTests(RouteTestBase):
    def test_agents_includes_guardrail_and_runtime_nodes(self) -> None:
        resp = self.client.get("/api/agents")
        self.assertEqual(resp.status_code, 200)
        agents = resp.json()
        self.assertIsInstance(agents, dict)
        # Guardrail display nodes and the two runtime nodes must be present.
        for key in ("input_guardrail", "content_guardrail", "setup", "output"):
            self.assertIn(key, agents, f"expected {key} in /api/agents payload")
        self.assertEqual(agents["input_guardrail"]["kind"], "guardrail")
        self.assertEqual(agents["content_guardrail"]["kind"], "guardrail")
        self.assertEqual(agents["setup"]["kind"], "runtime")
        self.assertEqual(agents["output"]["kind"], "runtime")
        # The payload should contain every sequence key from the registry.
        for key in registry.agent_sequence():
            self.assertIn(key, agents)

    def test_dropdown_returns_items_list(self) -> None:
        resp = self.client.get("/api/dropdown")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("items", body)
        self.assertIsInstance(body["items"], list)

    def test_rubric_returns_object(self) -> None:
        resp = self.client.get("/api/rubric")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), dict)

    def test_prompt_valid_name(self) -> None:
        # normalizer is a promptable agent with a real prompt file.
        resp = self.client.get("/api/prompts/normalizer")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.text.strip())

    def test_prompt_unknown_name_is_404(self) -> None:
        resp = self.client.get("/api/prompts/not-a-real-prompt")
        self.assertEqual(resp.status_code, 404)

    def test_prompt_name_rejects_path_traversal(self) -> None:
        # A traversal attempt is not in the allowlist -> 404 (never reaches disk).
        resp = self.client.get("/api/prompts/..%2f..%2fetc%2fpasswd")
        self.assertEqual(resp.status_code, 404)

    def test_evaluation_files_shape(self) -> None:
        resp = self.client.get("/api/evaluation/files")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("files", body)
        self.assertIsInstance(body["files"], list)
        for item in body["files"]:
            self.assertIn("name", item)
            self.assertIn("relativePath", item)
            self.assertIn("size", item)


class UploadRouteTests(RouteTestBase):
    def test_accepts_small_txt(self) -> None:
        resp = self.client.post(
            "/api/upload",
            files={"files": ("notes.txt", b"hello world", "text/plain")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["files"]), 1)
        self.assertEqual(body["files"][0]["originalName"], "notes.txt")

    def test_accepts_valid_pdf(self) -> None:
        resp = self.client.post(
            "/api/upload",
            files={"files": ("doc.pdf", _MINIMAL_PDF, "application/pdf")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()["files"]), 1)

    def test_rejects_too_many_files(self) -> None:
        files = [
            ("files", (f"f{i}.txt", b"x", "text/plain"))
            for i in range(state.MAX_UPLOAD_FILES + 1)
        ]
        resp = self.client.post("/api/upload", files=files)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Too many files", resp.json()["detail"])

    def test_unsupported_extension_is_rejected(self) -> None:
        # The route checks the ORIGINAL filename's extension against the allowlist
        # before safe_filename() can remap it, so a disallowed type is refused.
        resp = self.client.post(
            "/api/upload",
            files={"files": ("archive.zip", b"PK\x03\x04data", "application/zip")},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("Unsupported file type", resp.text)

    def test_rejects_pdf_with_bad_magic_bytes(self) -> None:
        # Declared .pdf but the bytes do not start with %PDF -> magic-byte guard.
        resp = self.client.post(
            "/api/upload",
            files={"files": ("fake.pdf", b"not a real pdf payload", "application/pdf")},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not appear to be a valid", resp.json()["detail"])

    def test_no_files_field_is_422(self) -> None:
        # Missing the required multipart field -> FastAPI validation error.
        resp = self.client.post("/api/upload")
        self.assertEqual(resp.status_code, 422)


class DownloadRouteTests(RouteTestBase):
    def test_unknown_workflow_is_404(self) -> None:
        resp = self.client.get("/api/download/does-not-exist/final_result.json")
        self.assertEqual(resp.status_code, 404)

    def test_download_zip_unknown_workflow_is_404(self) -> None:
        resp = self.client.get("/api/download-zip/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_download_zip_waits_for_terminal_status_and_task_cleanup(self) -> None:
        state.workflows["running-output"] = {
            "id": "running-output",
            "status": "running",
            "outputFolder": "unused",
        }
        response = self.client.get("/api/download-zip/running-output")
        self.assertEqual(response.status_code, 409)

        live_task = MagicMock()
        live_task.done.return_value = False
        state.workflows["cleanup-output"] = {
            "id": "cleanup-output",
            "status": "cancelled",
            "outputFolder": "unused",
        }
        state.workflow_tasks["cleanup-output"] = live_task
        response = self.client.get("/api/download-zip/cleanup-output")
        self.assertEqual(response.status_code, 409)

    def test_eval_batch_output_can_be_downloaded_as_zip(self) -> None:
        with TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "eval-batch-output"
            run_dir.mkdir()
            (run_dir / "evaluation_report.json").write_text("{}", encoding="utf-8")
            batch_id = "eval-batch-download"
            state.eval_batch_jobs[batch_id] = {
                "id": batch_id,
                "status": "completed",
                "outputFolder": "eval-batch-output",
            }
            with patch.object(files_module, "OUTPUTS_DIR", outputs_dir):
                response = self.client.get(f"/api/download-zip/{batch_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["content-type"], "application/zip")

    def test_download_zip_rebuilds_after_artifacts_change(self) -> None:
        with TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "mutable-output"
            run_dir.mkdir()
            (run_dir / "first.json").write_text("{}", encoding="utf-8")
            batch_id = "mutable-eval-batch"
            state.eval_batch_jobs[batch_id] = {
                "id": batch_id,
                "status": "completed",
                "outputFolder": "mutable-output",
            }
            with patch.object(files_module, "OUTPUTS_DIR", outputs_dir):
                first = self.client.get(f"/api/download-zip/{batch_id}")
                self.assertEqual(first.status_code, 200, first.text)
                (run_dir / "comparison.xlsx").write_bytes(b"new artifact")
                second = self.client.get(f"/api/download-zip/{batch_id}")

            self.assertEqual(second.status_code, 200, second.text)
            with zipfile.ZipFile(run_dir / "mutable-output.zip") as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["comparison.xlsx", "first.json"],
                )

    def test_output_artifact_download_is_contained_and_hides_server_path(self) -> None:
        with TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "batch"
            artifact = run_dir / "XLSX" / "compiled.xlsx"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"workbook")
            with (
                patch.object(files_module, "OUTPUTS_DIR", outputs_dir),
                patch.object(state, "OUTPUTS_DIR", outputs_dir),
            ):
                response = self.client.get(
                    "/api/output-artifact",
                    params={"folder": "batch", "file": "XLSX/compiled.xlsx"},
                )
                traversal = self.client.get(
                    "/api/output-artifact",
                    params={"folder": "batch", "file": "../outside.txt"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b"workbook")
            self.assertNotIn(str(outputs_dir), response.headers.get("content-disposition", ""))
            self.assertEqual(traversal.status_code, 400)

    def test_download_rejects_traversal_via_allowlist(self) -> None:
        # A traversal path is not in the workflow's downloadable allowlist, so it
        # is rejected before any filesystem access (404 File not found).
        from backend.src.paths import OUTPUTS_DIR

        out_dir = OUTPUTS_DIR / "wf-dl-traversal"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "final_result.json").write_text("{}", encoding="utf-8")
        try:
            wf_id = "wf-dl-traversal-id"
            record = state._new_workflow_record()
            record["status"] = "completed"
            record["outputFolder"] = "wf-dl-traversal"
            record["files"] = [{"name": "final_result.json", "downloadable": True}]
            state.workflows[wf_id] = record

            # Not in allowlist -> 404.
            resp = self.client.get(f"/api/download/{wf_id}/..%2f..%2fetc%2fpasswd")
            self.assertEqual(resp.status_code, 404)

            # Allowlisted file downloads fine.
            ok = self.client.get(f"/api/download/{wf_id}/final_result.json")
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json(), {})
        finally:
            import shutil

            shutil.rmtree(out_dir, ignore_errors=True)

    def test_download_missing_output_folder_is_404(self) -> None:
        wf_id = "wf-no-output"
        record = state._new_workflow_record()
        record["status"] = "completed"
        record["outputFolder"] = ""  # never set
        record["files"] = [{"name": "final_result.json", "downloadable": True}]
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/download/{wf_id}/final_result.json")
        self.assertEqual(resp.status_code, 404)


class WorkflowStatusResultRouteTests(RouteTestBase):
    def test_status_unknown_workflow_is_404(self) -> None:
        resp = self.client.get("/api/workflow/does-not-exist/status")
        self.assertEqual(resp.status_code, 404)

    def test_stream_unknown_workflow_is_404(self) -> None:
        resp = self.client.get("/api/workflow/does-not-exist/stream")
        self.assertEqual(resp.status_code, 404)

    def test_result_unknown_workflow_is_404(self) -> None:
        resp = self.client.get("/api/workflow/does-not-exist/result")
        self.assertEqual(resp.status_code, 404)

    def test_status_payload_shape(self) -> None:
        wf_id = "wf-status"
        record = state._new_workflow_record()
        record["status"] = "running"
        record["agents"]["normalizer"] = {
            "status": "thinking",
            "data": {
                "input": {"private": "course content"},
                "output": {"private": "generated content"},
                "startTime": "2026-07-27T12:00:00Z",
                "model": "gpt-test",
            },
        }
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/workflow/{wf_id}/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workflowId"], wf_id)
        self.assertEqual(body["status"], "running")
        self.assertIn("agents", body)
        self.assertIn("outputFolder", body)
        agent_data = body["agents"]["normalizer"]["data"]
        self.assertEqual(agent_data["model"], "gpt-test")
        self.assertNotIn("input", agent_data)
        self.assertNotIn("output", agent_data)

    def test_status_surfaces_blocked(self) -> None:
        wf_id = "wf-blocked"
        record = state._new_workflow_record()
        record["status"] = "blocked"
        record["blocked"] = {
            "stage": "input_guardrail",
            "reason": "off_topic",
            "message": "Not a physics question.",
        }
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/workflow/{wf_id}/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "blocked")
        self.assertIsInstance(body["blocked"], dict)
        self.assertEqual(body["blocked"]["reason"], "off_topic")

    def test_result_not_completed_is_400(self) -> None:
        wf_id = "wf-incomplete"
        record = state._new_workflow_record()
        record["status"] = "running"
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/workflow/{wf_id}/result")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not yet completed", resp.json()["detail"])

    def test_result_completed_shape(self) -> None:
        wf_id = "wf-complete"
        record = state._new_workflow_record()
        record["status"] = "completed"
        record["result"] = {"generated_questions": []}
        record["outputFolder"] = "06-30-2026/12-00-00"
        record["files"] = [{"name": "final_result.json", "downloadable": True}]
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/workflow/{wf_id}/result")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workflowId"], wf_id)
        self.assertEqual(body["result"], {"generated_questions": []})
        self.assertEqual(body["outputFolder"], "06-30-2026/12-00-00")
        self.assertEqual(body["files"], [{"name": "final_result.json", "downloadable": True}])
        self.assertIn("agents", body)


if __name__ == "__main__":
    unittest.main()

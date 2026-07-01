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

import unittest

from fastapi.testclient import TestClient

from backend.src import registry
from backend.src.api import state
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
        self._saved_batches = dict(state.batch_jobs)
        state.workflows.clear()
        state.batch_jobs.clear()

    def tearDown(self) -> None:
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_batches)


class HealthAndVersionRouteTests(RouteTestBase):
    def test_health_ok(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "healthy")
        self.assertIn("openai_api_key_configured", body)
        self.assertIn("uploads_dir", body)

    def test_version_json_returns_object(self) -> None:
        resp = self.client.get("/version.json")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), dict)
        self.assertIn("version", resp.json())


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
        state.workflows[wf_id] = record
        resp = self.client.get(f"/api/workflow/{wf_id}/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["workflowId"], wf_id)
        self.assertEqual(body["status"], "running")
        self.assertIn("agents", body)
        self.assertIn("outputFolder", body)

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


if __name__ == "__main__":
    unittest.main()

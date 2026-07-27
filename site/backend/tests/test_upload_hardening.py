"""Focused hardening tests for the ``POST /api/upload`` route.

Exercises the magic-byte signature map (``UPLOAD_MAGIC_SIGNATURES``), the
per-file cap (``MAX_UPLOAD_BYTES``), the per-request total cap
(``MAX_UPLOAD_TOTAL_BYTES``), and ``ALLOWED_UPLOAD_EXTENSIONS`` behavior.

``files.py`` binds these constants into its own module namespace at import
time, so the size-cap tests patch ``backend.src.api.files.<NAME>`` (patching
``state`` would not affect the already-bound name). All tests are offline;
uploads write into the real UPLOADS_DIR but the manifest/session dirs are
cleaned up by the app's own pruning on later runs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.src.api import files as files_module
from backend.src.api import state
from backend.src.server import app

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
_BINARY_UPLOADS = {
    "legacy.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload",
    "modern.docx": b"PK\x03\x04payload",
    "image.bmp": b"BMpayload",
    "image.gif": b"GIF89apayload",
    "image.jpg": b"\xff\xd8\xffpayload",
    "image.jpeg": b"\xff\xd8\xffpayload",
    "image.png": b"\x89PNG\r\n\x1a\npayload",
    "image.tif": b"II*\x00payload",
    "image.tiff": b"MM\x00*payload",
    "big-image.tif": b"II+\x00payload",
    "image.webp": b"RIFF\x04\x00\x00\x00WEBPpayload",
}


class UploadHardeningBase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._saved_workflows = dict(state.workflows)
        state.workflows.clear()

    def tearDown(self) -> None:
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)


class MagicSignatureTests(UploadHardeningBase):
    def test_signature_map_defines_pdf(self) -> None:
        self.assertIn(".pdf", state.UPLOAD_MAGIC_SIGNATURES)
        self.assertEqual(state.UPLOAD_MAGIC_SIGNATURES[".pdf"], (b"%PDF",))

    def test_pdf_with_correct_magic_accepted(self) -> None:
        resp = self.client.post(
            "/api/upload",
            files={"files": ("good.pdf", _MINIMAL_PDF, "application/pdf")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_pdf_with_leading_whitespace_before_magic_rejected(self) -> None:
        # Binary declarations require a true prefix, not a marker embedded
        # somewhere in the first KiB.
        resp = self.client.post(
            "/api/upload",
            files={"files": ("bad.pdf", b" \n%PDF-1.4", "application/pdf")},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not appear to be a valid", resp.json()["detail"])

    def test_common_binary_upload_prefixes_are_accepted(self) -> None:
        for filename, content in _BINARY_UPLOADS.items():
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/upload",
                    files={"files": (filename, content, "application/octet-stream")},
                )
                self.assertEqual(response.status_code, 200, response.text)

    def test_embedded_binary_marker_is_rejected(self) -> None:
        for filename, content in _BINARY_UPLOADS.items():
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/upload",
                    files={
                        "files": (
                            filename,
                            b"not-a-prefix" + content,
                            "application/octet-stream",
                        )
                    },
                )
                self.assertEqual(response.status_code, 400, response.text)

    def test_webp_requires_both_riff_and_webp_markers(self) -> None:
        response = self.client.post(
            "/api/upload",
            files={
                "files": (
                    "fake.webp",
                    b"RIFF\x04\x00\x00\x00NOPEpayload",
                    "image/webp",
                )
            },
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_input_to_agent_source_uses_same_prefix_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(state, "UPLOADS_DIR", root):
                accepted = state._save_input_to_agent_source_file(
                    workflow_id="accepted",
                    filename="questions.docx",
                    content=b"PK\x03\x04payload",
                )
                with self.assertRaises(HTTPException) as raised:
                    state._save_input_to_agent_source_file(
                        workflow_id="rejected",
                        filename="questions.docx",
                        content=b"junkPK\x03\x04payload",
                    )
        self.assertTrue(accepted.name.endswith(".docx"))
        self.assertEqual(raised.exception.status_code, 400)

    def test_txt_has_no_signature_requirement(self) -> None:
        # Types without an entry in UPLOAD_MAGIC_SIGNATURES skip the byte check.
        resp = self.client.post(
            "/api/upload",
            files={"files": ("plain.txt", b"anything at all", "text/plain")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)


class PerFileCapTests(UploadHardeningBase):
    def test_single_file_over_per_file_cap_is_413(self) -> None:
        with patch.object(files_module, "MAX_UPLOAD_BYTES", 8):
            resp = self.client.post(
                "/api/upload",
                files={"files": ("big.txt", b"0123456789ABCDEF", "text/plain")},
            )
        self.assertEqual(resp.status_code, 413)
        self.assertIn("File too large", resp.json()["detail"])

    def test_file_at_cap_is_accepted(self) -> None:
        # Exactly the cap is fine; only strictly-greater trips the 413.
        with patch.object(files_module, "MAX_UPLOAD_BYTES", 8):
            resp = self.client.post(
                "/api/upload",
                files={"files": ("ok.txt", b"01234567", "text/plain")},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["files"][0]["size"], 8)


class TotalRequestCapTests(UploadHardeningBase):
    def test_request_total_over_cap_is_413(self) -> None:
        # Each file stays under the per-file cap but their sum exceeds the
        # per-request total cap.
        with (
            patch.object(files_module, "MAX_UPLOAD_BYTES", 100),
            patch.object(files_module, "MAX_UPLOAD_TOTAL_BYTES", 10),
        ):
            resp = self.client.post(
                "/api/upload",
                files=[
                    ("files", ("a.txt", b"01234", "text/plain")),
                    ("files", ("b.txt", b"567890", "text/plain")),
                ],
            )
        self.assertEqual(resp.status_code, 413)
        self.assertIn("total request size limit", resp.json()["detail"])


class AllowedExtensionTests(UploadHardeningBase):
    def test_allowed_extensions_include_expected_document_types(self) -> None:
        for ext in (".pdf", ".txt", ".png", ".tex", ".md", ".docx"):
            self.assertIn(ext, state.ALLOWED_UPLOAD_EXTENSIONS)

    def test_disallowed_extension_is_rejected(self) -> None:
        # The route validates the ORIGINAL extension against the allowlist before
        # sanitizing, so a disallowed type is refused (not silently stored as .txt).
        resp = self.client.post(
            "/api/upload",
            files={"files": ("payload.bin", b"binary data", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("Unsupported file type", resp.text)

    def test_max_upload_files_constant(self) -> None:
        self.assertEqual(state.MAX_UPLOAD_FILES, 5)


if __name__ == "__main__":
    unittest.main()

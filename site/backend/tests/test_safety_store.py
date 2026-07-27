from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.safety import (
    create_unique_run_dir,
    ensure_child_path,
    safe_filename,
    unique_child_name,
)
from backend.src.workflow.store import WorkflowStore


class SafetyStoreTests(unittest.TestCase):
    def test_safe_filename_rejects_traversal_and_suffix_mismatch(self) -> None:
        self.assertEqual(
            safe_filename("../bad/name.pdf", fallback="isomorphic_questions.tex", allowed_suffixes={".tex"}),
            "name.tex",
        )
        self.assertEqual(
            safe_filename("notes final.tex", fallback="isomorphic_questions.tex", allowed_suffixes={".tex"}),
            "notes-final.tex",
        )

    def test_child_path_validation_rejects_absolute_hidden_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(ensure_child_path(root, "folder/file.txt"), (root / "folder/file.txt").resolve())
            for bad_path in ("../file.txt", "/tmp/file.txt", ".hidden/file.txt", "folder/../file.txt"):
                with self.subTest(bad_path=bad_path):
                    with self.assertRaises(ValueError):
                        ensure_child_path(root, bad_path)

    def test_unique_run_dir_handles_identical_timestamps_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "backend.src.safety.local_run_segments",
                return_value=("06-29-2026", "12-00-00"),
            ):
                first_path, first_label = create_unique_run_dir(root, "workflow-id")
                second_path, second_label = create_unique_run_dir(root, "workflow-id")
            self.assertTrue(first_path.is_dir())
            self.assertTrue(second_path.is_dir())
            self.assertNotEqual(first_label, second_label)
            # Runs are grouped into a per-day folder; same-second runs get -NN.
            self.assertEqual(first_label, "06-29-2026/12-00-00")
            self.assertEqual(second_label, "06-29-2026/12-00-00-01")
            self.assertEqual(first_path, root / "06-29-2026" / "12-00-00")

    def test_unique_child_name_handles_duplicate_upload_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "source.pdf").write_text("one", encoding="utf-8")
            used: set[str] = set()
            self.assertEqual(unique_child_name(root, "source.pdf", used), "source-2.pdf")
            self.assertEqual(unique_child_name(root, "source.pdf", used), "source-3.pdf")

    def test_workflow_store_is_visible_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "workflows.sqlite3"
            first = WorkflowStore(db_path)
            first.save("workflow", "wf-1", {"status": "running", "updated_at": "2026-05-05T00:00:00Z"})

            second = WorkflowStore(db_path)
            self.assertEqual(second.load("workflow", "wf-1")["status"], "running")
            second.save("workflow", "wf-1", {"status": "completed", "updated_at": "2026-05-05T00:01:00Z"})
            self.assertEqual(first.load("workflow", "wf-1")["status"], "completed")
            self.assertEqual([record_id for record_id, _ in second.list_records("workflow")], ["wf-1"])

    def test_workflow_store_keeps_database_and_sidecars_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "workflows.sqlite3"
            store = WorkflowStore(db_path)
            store.save("workflow", "wf-1", {"status": "running"})

            self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{db_path}{suffix}")
                if sidecar.exists():
                    self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_workflow_store_rejects_symlink_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "outside.sqlite3"
            target.write_bytes(b"do not overwrite")
            link = root / "workflows.sqlite3"
            link.symlink_to(target)

            with self.assertRaises(OSError):
                WorkflowStore(link)
            self.assertEqual(target.read_bytes(), b"do not overwrite")


if __name__ == "__main__":
    unittest.main()

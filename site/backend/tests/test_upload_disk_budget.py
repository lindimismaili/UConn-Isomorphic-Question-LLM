"""Regression coverage for live upload-filesystem admission control."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.src.api import eval_batch as eval_batch_module
from backend.src.api import files as files_module
from backend.src.api import state
from backend.src.api import workflows as workflows_module
from backend.src.api.upload_budget import UploadDiskBudget, upload_disk_budget
from backend.src.server import create_app
from backend.tests.support import IdempotentTestClient as TestClient


def test_actual_inventory_blocks_new_reservation_before_overrun() -> None:
    budget = UploadDiskBudget()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stored = root / "abandoned"
        stored.mkdir()
        (stored / "source.txt").write_bytes(b"12345678")

        with pytest.raises(HTTPException) as raised:
            budget.reserve(
                "next",
                root=root,
                session_dir=root / "next",
                requested_bytes=3,
                capacity_bytes=10,
            )

    assert raised.value.status_code == 507
    assert "Upload storage capacity" in raised.value.detail


def test_active_session_bytes_are_not_double_counted() -> None:
    budget = UploadDiskBudget()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = root / "first"
        second = root / "second"
        budget.reserve(
            "first",
            root=root,
            session_dir=first,
            requested_bytes=6,
            capacity_bytes=10,
        )
        first.mkdir()
        (first / "source.txt").write_bytes(b"123456")

        budget.reserve(
            "second",
            root=root,
            session_dir=second,
            requested_bytes=4,
            capacity_bytes=10,
        )
        budget.release("first")
        budget.release("second")


def test_concurrent_reservations_cannot_jointly_cross_capacity() -> None:
    budget = UploadDiskBudget()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def reserve(index: int) -> int:
            try:
                budget.reserve(
                    f"request-{index}",
                    root=root,
                    session_dir=root / f"request-{index}",
                    requested_bytes=7,
                    capacity_bytes=10,
                )
            except HTTPException as exc:
                return exc.status_code
            return 200

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(reserve, range(2)))

    assert statuses == [200, 507]


def test_reservation_growth_is_checked_before_the_next_write() -> None:
    budget = UploadDiskBudget()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stored = root / "stored"
        stored.mkdir()
        (stored / "source.txt").write_bytes(b"1234567")
        budget.reserve(
            "active",
            root=root,
            session_dir=root / "active",
            requested_bytes=2,
            capacity_bytes=10,
        )

        with pytest.raises(HTTPException) as raised:
            budget.ensure("active", 4, capacity_bytes=10)

    assert raised.value.status_code == 507


def test_chunk_growth_does_not_rescan_the_upload_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = UploadDiskBudget()
    scans = 0
    original_inventory = budget._inventory_size_locked

    def counted_inventory(root: Path) -> int:
        nonlocal scans
        scans += 1
        return original_inventory(root)

    monkeypatch.setattr(budget, "_inventory_size_locked", counted_inventory)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = root / "active"
        budget.reserve(
            "active",
            root=root,
            session_dir=session,
            requested_bytes=0,
            capacity_bytes=100,
        )
        session.mkdir()
        for size in range(1, 51):
            budget.ensure("active", size, capacity_bytes=100)
        (session / "source.txt").write_bytes(b"x" * 50)
        budget.release("active")

    assert scans == 1


def test_async_reserve_and_release_offload_filesystem_walks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = UploadDiskBudget()
    event_loop_thread = threading.get_ident()
    reserve_threads: list[int] = []
    release_threads: list[int] = []
    original_reserve = budget.reserve
    original_release = budget.release

    def observed_reserve(*args, **kwargs) -> None:
        reserve_threads.append(threading.get_ident())
        original_reserve(*args, **kwargs)

    def observed_release(*args, **kwargs) -> None:
        release_threads.append(threading.get_ident())
        original_release(*args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setattr(budget, "reserve", observed_reserve)
        asyncio.run(
            budget.reserve_async(
                "active",
                root=root,
                session_dir=root / "active",
                requested_bytes=0,
                capacity_bytes=100,
            )
        )
        monkeypatch.setattr(budget, "release", observed_release)
        asyncio.run(budget.release_async("active"))

    assert reserve_threads and reserve_threads[0] != event_loop_thread
    assert release_threads and release_threads[0] != event_loop_thread


def test_cancelled_async_reserve_does_not_leak_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = UploadDiskBudget()
    worker_started = threading.Event()
    worker_may_finish = threading.Event()
    original_reserve = budget.reserve

    def delayed_reserve(*args, **kwargs) -> None:
        worker_started.set()
        assert worker_may_finish.wait(timeout=5)
        original_reserve(*args, **kwargs)

    async def cancel_during_inventory(root: Path) -> None:
        task = asyncio.create_task(
            budget.reserve_async(
                "cancelled",
                root=root,
                session_dir=root / "cancelled",
                requested_bytes=1,
                capacity_bytes=100,
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 5)
        task.cancel()
        worker_may_finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setattr(budget, "reserve", delayed_reserve)
        asyncio.run(cancel_during_inventory(root))
        monkeypatch.setattr(budget, "reserve", original_reserve)
        budget.reserve(
            "cancelled",
            root=root,
            session_dir=root / "cancelled",
            requested_bytes=1,
            capacity_bytes=100,
        )
        budget.release("cancelled")


def test_upload_route_counts_abandoned_sessions_between_cleanup_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_disk_budget.reset_for_tests()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setattr(files_module, "UPLOADS_DIR", root)
        monkeypatch.setattr(files_module, "UPLOAD_MAX_BYTES", 10_000)
        client = TestClient(create_app())

        first = client.post(
            "/api/upload",
            files={"files": ("first.txt", b"x", "text/plain")},
        )
        assert first.status_code == 200, first.text
        stored_bytes = sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file()
        )

        # Leave the first valid upload unused. The next request can reserve its
        # file byte but must fail before writing manifest bytes past capacity.
        monkeypatch.setattr(files_module, "UPLOAD_MAX_BYTES", stored_bytes + 1)
        second = client.post(
            "/api/upload",
            files={"files": ("second.txt", b"y", "text/plain")},
        )

        assert second.status_code == 507, second.text
        assert len([path for path in root.iterdir() if path.is_dir()]) == 1
        assert sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file()
        ) <= stored_bytes + 1
    upload_disk_budget.reset_for_tests()


def test_artifact_cleanup_never_removes_an_in_progress_reserved_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_disk_budget.reset_for_tests()
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        uploads = workspace / "uploads"
        outputs = workspace / "outputs"
        uploads.mkdir()
        outputs.mkdir()
        session = uploads / "in-progress"
        session.mkdir()
        (session / "partial.txt").write_bytes(b"partial")
        old = time.time() - 10_000
        session.touch()
        os.utime(session, (old, old))
        upload_disk_budget.reserve(
            "active",
            root=uploads,
            session_dir=session,
            requested_bytes=7,
            capacity_bytes=100,
        )
        monkeypatch.setattr(state, "UPLOADS_DIR", uploads)
        monkeypatch.setattr(state, "OUTPUTS_DIR", outputs)
        monkeypatch.setattr(state, "UPLOAD_RETENTION_SECONDS", 1)
        monkeypatch.setattr(state, "UPLOAD_MAX_BYTES", 1)
        monkeypatch.setattr(state, "_last_artifact_cleanup_ts", 0)

        state._cleanup_artifact_directories(set(), set())

        assert session.is_dir()
        upload_disk_budget.release("active")
    upload_disk_budget.reset_for_tests()


def test_input_to_agent_source_upload_uses_shared_disk_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_disk_budget.reset_for_tests()
    saved_workflows = dict(state.workflows)
    state.workflows.clear()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stored = root / "stored"
            stored.mkdir()
            (stored / "existing.txt").write_bytes(b"x" * 10)
            monkeypatch.setattr(state, "UPLOADS_DIR", root)
            monkeypatch.setattr(state, "UPLOAD_MAX_BYTES", 10)
            client = TestClient(create_app())
            with patch.object(
                workflows_module,
                "get_openai_client",
                return_value=object(),
            ):
                response = client.post(
                    "/api/workflow/start-from-agent-input",
                    data={"startAgent": "normalizer"},
                    files={"agentInput": ("source.txt", b"physics", "text/plain")},
                )
        assert response.status_code == 507, response.text
    finally:
        state.workflows.clear()
        state.workflows.update(saved_workflows)
        upload_disk_budget.reset_for_tests()


def test_eval_pdf_upload_uses_shared_disk_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_disk_budget.reset_for_tests()
    saved_jobs = dict(state.eval_batch_jobs)
    state.eval_batch_jobs.clear()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stored = root / "stored"
            stored.mkdir()
            (stored / "existing.pdf").write_bytes(b"x" * 10)
            monkeypatch.setattr(eval_batch_module, "UPLOADS_DIR", root)
            monkeypatch.setattr(eval_batch_module, "UPLOAD_MAX_BYTES", 10)
            client = TestClient(create_app())
            with (
                patch.object(
                    eval_batch_module,
                    "get_openai_client",
                    return_value=object(),
                ),
                patch.object(eval_batch_module, "_ensure_batch_capacity"),
            ):
                response = client.post(
                    "/api/eval-batch/start",
                    data={
                        "mode": "upload",
                        "confirm_grader_calls": "true",
                    },
                    files=[
                        ("source_file", ("source.pdf", b"%PDF-source", "application/pdf")),
                        (
                            "generated_files",
                            ("generated.pdf", b"%PDF-generated", "application/pdf"),
                        ),
                    ],
                )
        assert response.status_code == 507, response.text
    finally:
        state.eval_batch_jobs.clear()
        state.eval_batch_jobs.update(saved_jobs)
        upload_disk_budget.reset_for_tests()

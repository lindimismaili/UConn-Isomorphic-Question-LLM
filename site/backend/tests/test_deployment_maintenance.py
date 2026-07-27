"""Cross-process paid-work admission and maintenance lock regressions."""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.src import deployment_maintenance
from backend.src.api import state


def _metadata(
    mode: int,
    *,
    uid: int = 0,
    gid: int = 0,
    device: int = 1,
    inode: int = 2,
    links: int = 1,
):
    return SimpleNamespace(
        st_mode=mode,
        st_uid=uid,
        st_gid=gid,
        st_dev=device,
        st_ino=inode,
        st_nlink=links,
    )


def test_lock_metadata_contract_requires_root_owned_stable_inode() -> None:
    parent = _metadata(stat.S_IFDIR | 0o755)
    opened = _metadata(stat.S_IFREG | 0o444)
    current = _metadata(stat.S_IFREG | 0o444)

    assert deployment_maintenance._paid_work_lock_metadata_is_safe(
        parent=parent,
        opened=opened,
        current=current,
    )
    opened.st_nlink = 2
    assert not deployment_maintenance._paid_work_lock_metadata_is_safe(
        parent=parent,
        opened=opened,
        current=current,
    )


def test_any_maintenance_marker_blocks_paid_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "maintenance"
    marker.write_text("schema=1\nreason=deployment\n", encoding="utf-8")
    monkeypatch.setattr(deployment_maintenance, "MAINTENANCE_FILE", marker)

    with pytest.raises(
        deployment_maintenance.DeploymentMaintenanceError,
        match="maintenance is active",
    ):
        deployment_maintenance.ensure_paid_work_allowed()


def test_installed_runtime_fails_closed_when_activity_lock_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed_root = tmp_path / "etc-uconn"
    deployed_root.mkdir()
    monkeypatch.setattr(
        deployment_maintenance,
        "MAINTENANCE_FILE",
        tmp_path / "maintenance",
    )
    monkeypatch.setattr(
        deployment_maintenance,
        "PAID_WORK_LOCK_FILE",
        tmp_path / "missing" / "activity.lock",
    )
    monkeypatch.setattr(
        deployment_maintenance,
        "DEPLOYED_CONFIG_ROOT",
        deployed_root,
    )

    with pytest.raises(
        deployment_maintenance.DeploymentMaintenanceError,
        match="activity lock is missing",
    ):
        deployment_maintenance.acquire_paid_work_lease()


def test_shared_paid_work_lease_conflicts_with_exclusive_backup_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_dir = tmp_path / "work-lock"
    lock_dir.mkdir()
    lock_path = lock_dir / "activity.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o444)
    monkeypatch.setattr(
        deployment_maintenance,
        "MAINTENANCE_FILE",
        tmp_path / "maintenance",
    )
    monkeypatch.setattr(
        deployment_maintenance,
        "PAID_WORK_LOCK_FILE",
        lock_path,
    )
    monkeypatch.setattr(
        deployment_maintenance,
        "DEPLOYED_CONFIG_ROOT",
        tmp_path / "not-installed",
    )
    monkeypatch.setattr(
        deployment_maintenance,
        "_paid_work_lock_metadata_is_safe",
        lambda **_kwargs: True,
    )

    lease = deployment_maintenance.acquire_paid_work_lease()
    exclusive_descriptor = os.open(lock_path, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(
                exclusive_descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        lease.close()
        fcntl.flock(
            exclusive_descriptor,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    finally:
        lease.close()
        os.close(exclusive_descriptor)


def test_workflow_reservation_holds_lease_until_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class _Lease:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(state, "acquire_paid_work_lease", _Lease)

    async def _run() -> None:
        owner = "maintenance-test-reservation"
        await state._reserve_workflow_capacity(owner)
        assert owner in state.paid_work_leases
        assert not closed
        await state._release_workflow_reservation(owner)
        assert owner not in state.paid_work_leases

    asyncio.run(_run())
    assert closed == [True]


def test_workflow_admission_rechecks_marker_after_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Lease:
        def close(self) -> None:
            pass

    monkeypatch.setattr(state, "acquire_paid_work_lease", _Lease)

    def block_after_reservation() -> None:
        raise deployment_maintenance.DeploymentMaintenanceError(
            "maintenance is active"
        )

    async def _run() -> None:
        owner = "maintenance-race-reservation"
        await state._reserve_workflow_capacity(owner)
        monkeypatch.setattr(
            state,
            "ensure_paid_work_allowed",
            block_after_reservation,
        )
        try:
            with pytest.raises(HTTPException) as error:
                await state._admit_workflow_record(
                    "maintenance-race-workflow",
                    state._new_workflow_record(),
                    reservation_id=owner,
                )
            assert error.value.status_code == 503
            assert "maintenance is active" in str(error.value.detail)
        finally:
            await state._release_workflow_reservation(owner)

    asyncio.run(_run())

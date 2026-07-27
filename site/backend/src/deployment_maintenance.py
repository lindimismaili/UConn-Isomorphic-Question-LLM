"""Cross-process admission guard for deployment and backup maintenance."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SERVICE_NAME = "uconn-isomorphic"
MAINTENANCE_FILE = Path(f"/run/{SERVICE_NAME}-deployment-maintenance")
PAID_WORK_LOCK_FILE = Path(
    f"/run/{SERVICE_NAME}-work-lock/activity.lock"
)
DEPLOYED_CONFIG_ROOT = Path(f"/etc/{SERVICE_NAME}")
_local_activity_condition = threading.Condition()
_local_activity_readers = 0
_local_activity_writer = False


class DeploymentMaintenanceError(RuntimeError):
    """Raised when new paid work cannot safely start during maintenance."""


def deployment_maintenance_active() -> bool:
    """Return true for a present or unsafe maintenance marker."""
    try:
        MAINTENANCE_FILE.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def ensure_paid_work_allowed() -> None:
    """Fail closed while the deployment/backup maintenance marker exists."""
    if deployment_maintenance_active():
        raise DeploymentMaintenanceError(
            "Deployment or backup maintenance is active; no new paid work may start."
        )


@dataclass
class PaidWorkLease:
    """One shared activity-lock lease held through paid-work cleanup."""

    descriptor: int | None
    local_mode: str = ""

    def close(self) -> None:
        descriptor, self.descriptor = self.descriptor, None
        local_mode, self.local_mode = self.local_mode, ""
        if local_mode:
            global _local_activity_readers, _local_activity_writer
            with _local_activity_condition:
                if local_mode == "shared":
                    _local_activity_readers = max(0, _local_activity_readers - 1)
                else:
                    _local_activity_writer = False
                _local_activity_condition.notify_all()
        if descriptor is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _paid_work_lock_metadata_is_safe(
    *,
    parent: os.stat_result,
    opened: os.stat_result,
    current: os.stat_result,
) -> bool:
    """Validate the installed lock's immutable path and ownership contract."""
    return (
        stat.S_ISDIR(parent.st_mode)
        and not stat.S_ISLNK(parent.st_mode)
        and parent.st_uid == 0
        and parent.st_gid == 0
        and stat.S_IMODE(parent.st_mode) == 0o755
        and stat.S_ISREG(opened.st_mode)
        and not stat.S_ISLNK(current.st_mode)
        and opened.st_uid == 0
        and opened.st_gid == 0
        and stat.S_IMODE(opened.st_mode) == 0o444
        and opened.st_nlink == 1
        and current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
    )


def _open_validated_activity_lock() -> int | None:
    """Open the installed activity lock, or select local development locking."""
    try:
        parent_metadata = PAID_WORK_LOCK_FILE.parent.lstat()
        descriptor = os.open(
            PAID_WORK_LOCK_FILE,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        if DEPLOYED_CONFIG_ROOT.exists():
            raise DeploymentMaintenanceError(
                "The installed paid-work activity lock is missing; "
                "new paid work was refused."
            ) from None
        return None
    except DeploymentMaintenanceError:
        raise
    except OSError as exc:
        raise DeploymentMaintenanceError(
            "The paid-work activity lock cannot be opened safely."
        ) from exc

    try:
        opened = os.fstat(descriptor)
        current = PAID_WORK_LOCK_FILE.lstat()
        if not _paid_work_lock_metadata_is_safe(
            parent=parent_metadata,
            opened=opened,
            current=current,
        ):
            raise DeploymentMaintenanceError(
                "The paid-work activity lock or its directory has unsafe ownership, "
                "type, permissions, links, or identity."
            )
        return descriptor
    except BaseException:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise


def _acquire_local_activity_shared() -> PaidWorkLease:
    global _local_activity_readers
    with _local_activity_condition:
        if _local_activity_writer:
            raise DeploymentMaintenanceError(
                "Artifact cleanup or mutation is active; new work was refused."
            )
        _local_activity_readers += 1
    return PaidWorkLease(None, local_mode="shared")


def _try_acquire_local_activity_exclusive() -> PaidWorkLease | None:
    global _local_activity_writer
    with _local_activity_condition:
        if _local_activity_writer or _local_activity_readers:
            return None
        _local_activity_writer = True
    return PaidWorkLease(None, local_mode="exclusive")


def acquire_paid_work_lease() -> PaidWorkLease:
    """Acquire and validate the shared activity lock without blocking."""
    ensure_paid_work_allowed()
    descriptor = _open_validated_activity_lock()
    if descriptor is None:
        lease = _acquire_local_activity_shared()
        try:
            ensure_paid_work_allowed()
        except BaseException:
            lease.close()
            raise
        return lease
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentMaintenanceError(
                "A deployment, backup, restore, artifact cleanup, or artifact "
                "mutation is active; new work was refused."
            ) from exc
        ensure_paid_work_allowed()
        return PaidWorkLease(descriptor)
    except BaseException:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise


def try_acquire_artifact_mutation_lease() -> PaidWorkLease | None:
    """Try to exclude paid work/readers while mutating or pruning artifacts."""
    ensure_paid_work_allowed()
    descriptor = _open_validated_activity_lock()
    if descriptor is None:
        lease = _try_acquire_local_activity_exclusive()
        if lease is None:
            return None
        try:
            ensure_paid_work_allowed()
        except BaseException:
            lease.close()
            raise
        return lease
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        ensure_paid_work_allowed()
        return PaidWorkLease(descriptor)
    except BaseException:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise


@contextlib.contextmanager
def cli_paid_work_guard() -> Iterator[None]:
    """Hold a shared deploy lock for one paid CLI command.

    A normal development checkout has no device lock file and only receives
    the marker check. On an installed Raspberry Pi, the professor belongs to
    the lock file's read group; backup/deploy operations take the conflicting
    exclusive lock before entering maintenance.
    """
    lease = acquire_paid_work_lease()
    try:
        yield
    finally:
        lease.close()


@contextlib.contextmanager
def cli_artifact_access_guard() -> Iterator[None]:
    """Hold a shared activity lease while a CLI command reads artifacts."""
    lease = acquire_paid_work_lease()
    try:
        yield
    finally:
        lease.close()


@contextlib.contextmanager
def cli_artifact_mutation_guard() -> Iterator[None]:
    """Exclusively protect one CLI artifact writer from cleanup/read races."""
    lease = try_acquire_artifact_mutation_lease()
    if lease is None:
        raise DeploymentMaintenanceError(
            "Artifacts are currently in use; retry this command after active work finishes."
        )
    try:
        yield
    finally:
        lease.close()

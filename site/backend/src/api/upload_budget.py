"""Process-local admission control for the mutable upload filesystem."""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException

from backend.src.provider_timeouts import await_cancellation_safe_blocking_call


@dataclass
class _Reservation:
    root: Path
    session_dir: Path
    bytes_reserved: int


class UploadDiskBudget:
    """Atomically account for stored uploads and in-progress upload requests.

    Active session directories are excluded from the disk inventory and
    represented by their reservation instead. This avoids double-counting
    bytes already written by an in-progress request while still preventing
    concurrent requests from jointly crossing the configured capacity.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reservations: dict[str, _Reservation] = {}
        self._committed_bytes: dict[Path, int] = {}

    @staticmethod
    def _normalized_paths(root: Path, session_dir: Path) -> tuple[Path, Path]:
        normalized_root = root.resolve()
        normalized_session = session_dir.resolve(strict=False)
        try:
            relative = normalized_session.relative_to(normalized_root)
        except ValueError as exc:
            raise ValueError("Upload session must stay beneath the upload root") from exc
        if not relative.parts:
            raise ValueError("Upload session cannot be the upload root")
        return normalized_root, normalized_session

    def _inventory_size_locked(self, root: Path) -> int:
        excluded = {
            reservation.session_dir
            for reservation in self._reservations.values()
            if reservation.root == root
        }
        total = 0
        if not root.is_dir():
            return total
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory).resolve(strict=False)
            kept_dirs: list[str] = []
            for dirname in dirnames:
                child = (directory_path / dirname).resolve(strict=False)
                if child not in excluded:
                    kept_dirs.append(dirname)
            dirnames[:] = kept_dirs
            for filename in filenames:
                path = directory_path / filename
                if path.is_symlink():
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _usage_locked(self, root: Path) -> int:
        reserved = sum(
            reservation.bytes_reserved
            for reservation in self._reservations.values()
            if reservation.root == root
        )
        return self._committed_bytes.get(root, 0) + reserved

    def _reconcile_root_locked(self, root: Path) -> None:
        """Refresh committed bytes once, excluding all active session dirs."""
        self._committed_bytes[root] = self._inventory_size_locked(root)

    @staticmethod
    def _session_size(path: Path) -> int:
        """Measure one completed session without rescanning the upload root."""
        total = 0
        if not path.is_dir():
            return total
        for child in path.rglob("*"):
            if child.is_symlink() or not child.is_file():
                continue
            try:
                total += child.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _raise_full() -> None:
        raise HTTPException(
            status_code=507,
            detail=(
                "Upload storage capacity has been reached. Remove expired upload "
                "sessions or increase UPLOAD_MAX_BYTES before retrying."
            ),
        )

    def reserve(
        self,
        reservation_id: str,
        *,
        root: Path,
        session_dir: Path,
        requested_bytes: int,
        capacity_bytes: int,
    ) -> None:
        """Reserve bytes for one request before its session is written."""
        if not reservation_id:
            raise ValueError("Upload reservation id is required")
        if requested_bytes < 0 or capacity_bytes < 1:
            raise ValueError("Upload reservation sizes must be non-negative")
        normalized_root, normalized_session = self._normalized_paths(root, session_dir)
        with self._lock:
            if reservation_id in self._reservations:
                raise ValueError("Upload reservation id is already active")
            for reservation in self._reservations.values():
                if reservation.root != normalized_root:
                    continue
                if (
                    normalized_session == reservation.session_dir
                    or normalized_session in reservation.session_dir.parents
                    or reservation.session_dir in normalized_session.parents
                ):
                    raise ValueError("Upload session overlaps an active reservation")
            reservation = _Reservation(
                root=normalized_root,
                session_dir=normalized_session,
                bytes_reserved=0,
            )
            self._reservations[reservation_id] = reservation
            try:
                self._reconcile_root_locked(normalized_root)
                if self._usage_locked(normalized_root) + requested_bytes > capacity_bytes:
                    self._raise_full()
                reservation.bytes_reserved = requested_bytes
            except BaseException:
                self._reservations.pop(reservation_id, None)
                self._reconcile_root_locked(normalized_root)
                raise

    async def reserve_async(
        self,
        reservation_id: str,
        *,
        root: Path,
        session_dir: Path,
        requested_bytes: int,
        capacity_bytes: int,
    ) -> None:
        """Reserve without walking the upload tree on the event-loop thread.

        If the request is cancelled while the worker is still reconciling disk
        usage, wait for that worker and release a successfully acquired
        reservation before propagating cancellation.
        """
        try:
            await await_cancellation_safe_blocking_call(
                asyncio.to_thread,
                self.reserve,
                reservation_id,
                root=root,
                session_dir=session_dir,
                requested_bytes=requested_bytes,
                capacity_bytes=capacity_bytes,
            )
        except asyncio.CancelledError as cancellation:
            try:
                await self.release_async(reservation_id)
            except asyncio.CancelledError:
                pass
            raise cancellation

    def ensure(self, reservation_id: str, requested_bytes: int, *, capacity_bytes: int) -> None:
        """Grow an active reservation before additional bytes are written."""
        if requested_bytes < 0 or capacity_bytes < 1:
            raise ValueError("Upload reservation sizes must be non-negative")
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise RuntimeError("Upload reservation is not active")
            if requested_bytes <= reservation.bytes_reserved:
                return
            additional = requested_bytes - reservation.bytes_reserved
            if self._usage_locked(reservation.root) + additional > capacity_bytes:
                self._raise_full()
            reservation.bytes_reserved = requested_bytes

    def release(self, reservation_id: str) -> None:
        """Release a request reservation; persisted files become inventory."""
        with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            if reservation is None:
                return
            self._committed_bytes[reservation.root] = (
                self._committed_bytes.get(reservation.root, 0)
                + self._session_size(reservation.session_dir)
            )

    async def release_async(self, reservation_id: str) -> None:
        """Release without scanning the completed session on the event loop."""
        await await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            self.release,
            reservation_id,
        )

    @contextmanager
    def cleanup_guard(self, root: Path) -> Iterator[set[str]]:
        """Serialize cleanup with admission and expose reserved session labels."""
        normalized_root = root.resolve()
        with self._lock:
            labels = {
                reservation.session_dir.relative_to(normalized_root).as_posix()
                for reservation in self._reservations.values()
                if reservation.root == normalized_root
            }
            try:
                yield labels
            finally:
                self._reconcile_root_locked(normalized_root)

    def reset_for_tests(self) -> None:
        """Clear process-local reservations between isolated tests."""
        with self._lock:
            self._reservations.clear()
            self._committed_bytes.clear()


upload_disk_budget = UploadDiskBudget()

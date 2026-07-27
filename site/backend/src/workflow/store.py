"""SQLite-backed workflow state persistence."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any


class WorkflowStore:
    """Small JSON-record SQLite store for workflow and batch status."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_database_file()
        self._init_db()

    def _prepare_database_file(self) -> None:
        """Create or validate the state database without exposing its payload."""
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"Workflow store is not a regular file: {self.path}") from None
        else:
            os.close(descriptor)
        os.chmod(self.path, 0o600, follow_symlinks=False)

    def _secure_sqlite_files(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError(f"Workflow store sidecar is not a regular file: {candidate}")
            os.chmod(candidate, 0o600, follow_symlinks=False)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        self._secure_sqlite_files()
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    response TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def save(self, kind: str, record_id: str, payload: dict[str, Any]) -> None:
        updated_at = str(payload.get("updated_at") or "")
        body = json.dumps(payload, ensure_ascii=False, default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO records (kind, id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind, id) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (kind, record_id, body, updated_at),
            )

    def load(self, kind: str, record_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM records WHERE kind = ? AND id = ?",
                (kind, record_id),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def delete(self, kind: str, record_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM records WHERE kind = ? AND id = ?",
                (kind, record_id),
            )

    def list_records(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM records WHERE kind = ? ORDER BY updated_at ASC",
                (kind,),
            ).fetchall()
        records: list[tuple[str, dict[str, Any]]] = []
        for record_id, raw_payload in rows:
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append((str(record_id), payload))
        return records

    def claim_idempotency(
        self,
        *,
        key: str,
        endpoint: str,
        fingerprint: str,
        resource_kind: str,
        resource_id: str,
        response: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically claim a paid-start key or return its existing record."""
        response_body = json.dumps(response, ensure_ascii=False, default=str)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO idempotency_keys (
                    key,
                    endpoint,
                    fingerprint,
                    resource_kind,
                    resource_id,
                    response,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    key,
                    endpoint,
                    fingerprint,
                    resource_kind,
                    resource_id,
                    response_body,
                    time.time(),
                ),
            )
            row = connection.execute(
                """
                SELECT endpoint, fingerprint, resource_kind, resource_id,
                       response, status, created_at
                FROM idempotency_keys
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Idempotency claim disappeared")
        try:
            parsed_response = json.loads(row[4])
        except json.JSONDecodeError:
            parsed_response = {}
        record = {
            "key": key,
            "endpoint": str(row[0]),
            "fingerprint": str(row[1]),
            "resource_kind": str(row[2]),
            "resource_id": str(row[3]),
            "response": parsed_response if isinstance(parsed_response, dict) else {},
            "status": str(row[5]),
            "created_at": float(row[6]),
        }
        return cursor.rowcount == 1, record

    def load_idempotency(self, key: str) -> dict[str, Any] | None:
        """Load one durable idempotency record."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT endpoint, fingerprint, resource_kind, resource_id,
                       response, status, created_at
                FROM idempotency_keys
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        try:
            parsed_response = json.loads(row[4])
        except json.JSONDecodeError:
            parsed_response = {}
        return {
            "key": key,
            "endpoint": str(row[0]),
            "fingerprint": str(row[1]),
            "resource_kind": str(row[2]),
            "resource_id": str(row[3]),
            "response": parsed_response if isinstance(parsed_response, dict) else {},
            "status": str(row[5]),
            "created_at": float(row[6]),
        }

    def commit_idempotency(
        self,
        *,
        key: str,
        endpoint: str,
        fingerprint: str,
        resource_id: str,
    ) -> None:
        """Mark an owned idempotency claim as durably accepted."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE idempotency_keys
                SET status = 'committed'
                WHERE key = ?
                  AND endpoint = ?
                  AND fingerprint = ?
                  AND resource_id = ?
                  AND status = 'pending'
                """,
                (key, endpoint, fingerprint, resource_id),
            )
        if cursor.rowcount != 1:
            record = self.load_idempotency(key)
            if (
                record is None
                or record["endpoint"] != endpoint
                or record["fingerprint"] != fingerprint
                or record["resource_id"] != resource_id
                or record["status"] != "committed"
            ):
                raise RuntimeError("Idempotency claim could not be committed")

    def abandon_idempotency(
        self,
        *,
        key: str,
        endpoint: str,
        fingerprint: str,
        resource_id: str,
    ) -> None:
        """Delete an uncommitted claim when no resource was admitted."""
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM idempotency_keys
                WHERE key = ?
                  AND endpoint = ?
                  AND fingerprint = ?
                  AND resource_id = ?
                  AND status = 'pending'
                """,
                (key, endpoint, fingerprint, resource_id),
            )

    def reconcile_idempotency(self) -> tuple[int, int]:
        """Resolve claims left pending by the previous single-process server.

        This must run during exclusive application startup, before the server
        accepts requests. A matching resource proves that the paid start was
        admitted, so its claim is committed. A pending claim without a
        resource was interrupted before admission and is released.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            committed = connection.execute(
                """
                UPDATE idempotency_keys
                SET status = 'committed'
                WHERE status = 'pending'
                  AND EXISTS (
                      SELECT 1
                      FROM records
                      WHERE records.kind = idempotency_keys.resource_kind
                        AND records.id = idempotency_keys.resource_id
                  )
                """
            )
            abandoned = connection.execute(
                """
                DELETE FROM idempotency_keys
                WHERE status = 'pending'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM records
                      WHERE records.kind = idempotency_keys.resource_kind
                        AND records.id = idempotency_keys.resource_id
                  )
                """
            )
        return (
            max(0, int(committed.rowcount)),
            max(0, int(abandoned.rowcount)),
        )

    def prune_idempotency(self, *, older_than: float) -> int:
        """Remove old mappings only after their durable resource is gone."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM idempotency_keys
                WHERE created_at < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM records
                      WHERE records.kind = idempotency_keys.resource_kind
                        AND records.id = idempotency_keys.resource_id
                  )
                """,
                (float(older_than),),
            )
        return max(0, int(cursor.rowcount))

    def delete_idempotency(self, key: str) -> None:
        """Delete one mapping (primarily for scoped test and admin cleanup)."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM idempotency_keys WHERE key = ?",
                (key,),
            )

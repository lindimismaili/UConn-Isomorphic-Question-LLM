"""SQLite-backed workflow state persistence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class WorkflowStore:
    """Small JSON-record SQLite store for workflow and batch status."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
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


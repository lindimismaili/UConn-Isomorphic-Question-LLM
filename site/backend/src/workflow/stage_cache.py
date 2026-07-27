"""Optional per-stage output cache (test feature, off by default).

When ``ENABLE_STAGE_CACHE`` is truthy the engine can reuse a prior stage's
output instead of calling the model, keyed on that stage's exact input plus the
model, reasoning effort, and prompt text. This skips regenerating identical
requests and saves token cost. When the toggle is off the ``StageCache`` is
inert and the engine behaves exactly as before.

Matching is exact-content for now. ``lookup``/``store`` are kept behind a small
interface so a semantic backend (embed the input, nearest-neighbor over stored
keys) can be added later without touching the engine call sites. Cached records
are versioned and bounded by ``STAGE_CACHE_TTL_SECONDS``,
``STAGE_CACHE_MAX_ENTRIES``, and ``STAGE_CACHE_MAX_RECORD_BYTES``. Each setting
has a finite hard ceiling so this optional cache cannot become unbounded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..paths import RUNTIME_DIR
from ..safety import stable_short_hash
from .store import WorkflowStore

_CACHE_KIND = "stage_cache"
_TRUTHY = {"1", "true", "yes", "on"}
_CACHE_SCHEMA_VERSION = 2
_CACHE_KEY_VERSION = f"stage-cache-v{_CACHE_SCHEMA_VERSION}"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_MAX_TTL_SECONDS = 30 * 24 * 60 * 60
_DEFAULT_MAX_ENTRIES = 128
_MAX_MAX_ENTRIES = 1_000
_DEFAULT_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_MAX_RECORD_BYTES = 16 * 1024 * 1024
_MAX_CLOCK_SKEW_SECONDS = 5 * 60
STAGE_CACHE_DB_PATH = RUNTIME_DIR / "stage_cache.sqlite3"


def stage_cache_enabled() -> bool:
    """Return True when the ENABLE_STAGE_CACHE toggle is set to a truthy value."""
    # The operations dashboard can persist an override without changing the
    # deployment's environment file. Import lazily to keep this low-level
    # module free of API/state import cycles.
    try:
        from backend.src.operations import operations

        configured = operations.get_setting("stage_cache_enabled", None)
        if isinstance(configured, bool):
            return configured
    except Exception:
        # A cache toggle must never prevent a workflow from starting.
        pass
    return (os.getenv("ENABLE_STAGE_CACHE", "false") or "").strip().lower() in _TRUTHY


def _bounded_env_int(name: str, *, default: int, maximum: int) -> int:
    """Read a positive integer setting while preserving a finite safety ceiling."""
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, maximum)


def _bounded_setting(value: Optional[int], *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Stage-cache limits must be positive integers")
    return min(value, maximum)


def file_content_hash(path: Path) -> str:
    """Return a short stable hash of a file's bytes (for cache keys).

    Blocking I/O; call through the engine's ``to_thread`` from async paths.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


class StageCache:
    """Exact-match per-stage output cache backed by the generic SQLite store."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        store: Optional[WorkflowStore] = None,
        ttl_seconds: Optional[int] = None,
        max_entries: Optional[int] = None,
        max_record_bytes: Optional[int] = None,
    ) -> None:
        self.enabled = stage_cache_enabled() if enabled is None else enabled
        self.ttl_seconds = _bounded_setting(
            ttl_seconds,
            default=_bounded_env_int(
                "STAGE_CACHE_TTL_SECONDS",
                default=_DEFAULT_TTL_SECONDS,
                maximum=_MAX_TTL_SECONDS,
            ),
            maximum=_MAX_TTL_SECONDS,
        )
        self.max_entries = _bounded_setting(
            max_entries,
            default=_bounded_env_int(
                "STAGE_CACHE_MAX_ENTRIES",
                default=_DEFAULT_MAX_ENTRIES,
                maximum=_MAX_MAX_ENTRIES,
            ),
            maximum=_MAX_MAX_ENTRIES,
        )
        self.max_record_bytes = _bounded_setting(
            max_record_bytes,
            default=_bounded_env_int(
                "STAGE_CACHE_MAX_RECORD_BYTES",
                default=_DEFAULT_MAX_RECORD_BYTES,
                maximum=_MAX_MAX_RECORD_BYTES,
            ),
            maximum=_MAX_MAX_RECORD_BYTES,
        )
        if store is not None:
            self._store: Optional[WorkflowStore] = store
        elif self.enabled:
            try:
                self._store = WorkflowStore(STAGE_CACHE_DB_PATH)
            except sqlite3.DatabaseError:
                self.enabled = False
                self._store = None
        else:
            self._store = None
        if self.enabled and self._store is not None:
            try:
                self._store.path.chmod(0o600)
            except OSError:
                pass
            self.prune()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Open one cross-process-safe write transaction for cache maintenance."""
        if self._store is None:
            raise RuntimeError("Stage cache has no backing store")
        connection = sqlite3.connect(str(self._store.path), timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record_timestamp(record: dict[str, Any], name: str) -> Optional[float]:
        value = record.get(name)
        if isinstance(value, bool):
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        return timestamp if math.isfinite(timestamp) else None

    def _decode_record(
        self,
        *,
        key: str,
        raw_payload: Any,
        now: float,
    ) -> Optional[dict[str, Any]]:
        if not isinstance(raw_payload, str):
            return None
        try:
            encoded_size = len(raw_payload.encode("utf-8"))
        except UnicodeEncodeError:
            return None
        if encoded_size > self.max_record_bytes:
            return None
        try:
            record = json.loads(raw_payload)
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        if record.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        if record.get("key_version") != _CACHE_KEY_VERSION:
            return None
        if record.get("cache_key") != key:
            return None
        if not isinstance(record.get("agent_key"), str) or not record["agent_key"]:
            return None
        updated_at = self._record_timestamp(record, "updated_at_epoch")
        expires_at = self._record_timestamp(record, "expires_at_epoch")
        if updated_at is None or expires_at is None:
            return None
        if updated_at > now + _MAX_CLOCK_SKEW_SECONDS or expires_at <= updated_at:
            return None
        effective_expiry = min(expires_at, updated_at + self.ttl_seconds)
        if effective_expiry <= now:
            return None
        output = record.get("output")
        return record if isinstance(output, dict) else None

    def _prune_in_transaction(self, connection: sqlite3.Connection, *, now: float) -> None:
        valid_count = 0
        last_id: Optional[str] = None
        while True:
            if last_id is None:
                rows = connection.execute(
                    """
                    SELECT id, payload
                    FROM records
                    WHERE kind = ?
                    ORDER BY id ASC
                    LIMIT 100
                    """,
                    (_CACHE_KIND,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, payload
                    FROM records
                    WHERE kind = ? AND id > ?
                    ORDER BY id ASC
                    LIMIT 100
                    """,
                    (_CACHE_KIND, last_id),
                ).fetchall()
            if not rows:
                break
            last_id = str(rows[-1][0])
            invalid_ids: list[str] = []
            for record_id, raw_payload in rows:
                key = str(record_id)
                record = self._decode_record(key=key, raw_payload=raw_payload, now=now)
                if record is None:
                    invalid_ids.append(key)
                else:
                    valid_count += 1
            if invalid_ids:
                connection.executemany(
                    "DELETE FROM records WHERE kind = ? AND id = ?",
                    ((_CACHE_KIND, key) for key in invalid_ids),
                )

        overflow = valid_count - self.max_entries
        if overflow > 0:
            connection.execute(
                """
                DELETE FROM records
                WHERE kind = ? AND id IN (
                    SELECT id
                    FROM records
                    WHERE kind = ?
                    ORDER BY updated_at ASC, id ASC
                    LIMIT ?
                )
                """,
                (_CACHE_KIND, _CACHE_KIND, overflow),
            )

    def prune(self) -> None:
        """Evict expired, invalid, and least-recent entries over the configured cap."""
        if not self.enabled or self._store is None:
            return
        try:
            with self._transaction() as connection:
                self._prune_in_transaction(
                    connection,
                    now=datetime.now(timezone.utc).timestamp(),
                )
        except sqlite3.DatabaseError:
            return

    def make_key(
        self,
        agent_key: str,
        input_payload: Any,
        *,
        model: str = "",
        reasoning: str = "",
        instructions: str = "",
        extra: str = "",
    ) -> str:
        """Build a deterministic cache key for a stage invocation.

        The key folds in the canonicalized input payload, the model, the
        reasoning effort, a hash of the agent's instructions (so a prompt or
        strategy change invalidates naturally), and any ``extra`` discriminator
        (e.g. source-file content hashes for the extracter).
        """
        canonical = json.dumps(input_payload, sort_keys=True, ensure_ascii=False, default=str)
        instr = stable_short_hash(instructions, 64) if instructions else ""
        raw = "\x1f".join(
            [
                _CACHE_KEY_VERSION,
                agent_key,
                model or "",
                reasoning or "",
                instr,
                extra or "",
                canonical,
            ]
        )
        return f"{_CACHE_KEY_VERSION}:{stable_short_hash(raw, 40)}"

    def lookup(self, key: str) -> Optional[dict[str, Any]]:
        """Return the cached stage output dump for ``key``, or None on a miss."""
        if not self.enabled or self._store is None or not key:
            return None
        try:
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT payload FROM records WHERE kind = ? AND id = ?",
                    (_CACHE_KIND, key),
                ).fetchone()
                if row is None:
                    return None
                record = self._decode_record(
                    key=key,
                    raw_payload=row[0],
                    now=datetime.now(timezone.utc).timestamp(),
                )
                if record is None:
                    connection.execute(
                        "DELETE FROM records WHERE kind = ? AND id = ?",
                        (_CACHE_KIND, key),
                    )
                    return None
                return record["output"]
        except sqlite3.DatabaseError:
            return None

    def store(
        self,
        key: str,
        *,
        agent_key: str,
        output: dict[str, Any],
        model: str = "",
        reasoning: str = "",
    ) -> None:
        """Persist a stage output dump under ``key`` (no-op when disabled)."""
        if not self.enabled or self._store is None or not key:
            return
        now = datetime.now(timezone.utc)
        now_epoch = now.timestamp()
        record = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "key_version": _CACHE_KEY_VERSION,
            "cache_key": key,
            "agent_key": agent_key,
            "model": model,
            "reasoning": reasoning,
            "output": output,
            "updated_at": now.isoformat(),
            "updated_at_epoch": now_epoch,
            "expires_at_epoch": now_epoch + self.ttl_seconds,
        }
        try:
            body = json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))
            body_size = len(body.encode("utf-8"))
        except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
            body = ""
            body_size = self.max_record_bytes + 1

        try:
            with self._transaction() as connection:
                if body_size > self.max_record_bytes:
                    connection.execute(
                        "DELETE FROM records WHERE kind = ? AND id = ?",
                        (_CACHE_KIND, key),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO records (kind, id, payload, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(kind, id) DO UPDATE SET
                            payload=excluded.payload,
                            updated_at=excluded.updated_at
                        """,
                        (_CACHE_KIND, key, body, now.isoformat()),
                    )
                self._prune_in_transaction(connection, now=now_epoch)
        except sqlite3.DatabaseError:
            return

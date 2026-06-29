"""Optional per-stage output cache (test feature, off by default).

When ``ENABLE_STAGE_CACHE`` is truthy the engine can reuse a prior stage's
output instead of calling the model, keyed on that stage's exact input plus the
model, reasoning effort, and prompt text. This skips regenerating identical
requests and saves token cost. When the toggle is off the ``StageCache`` is
inert and the engine behaves exactly as before.

Matching is exact-content for now. ``lookup``/``store`` are kept behind a small
interface so a semantic backend (embed the input, nearest-neighbor over stored
keys) can be added later without touching the engine call sites.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..paths import RUNTIME_DIR
from ..safety import stable_short_hash
from .store import WorkflowStore

_CACHE_KIND = "stage_cache"
_TRUTHY = {"1", "true", "yes", "on"}
STAGE_CACHE_DB_PATH = RUNTIME_DIR / "stage_cache.sqlite3"


def stage_cache_enabled() -> bool:
    """Return True when the ENABLE_STAGE_CACHE toggle is set to a truthy value."""
    return (os.getenv("ENABLE_STAGE_CACHE", "false") or "").strip().lower() in _TRUTHY


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
    ) -> None:
        self.enabled = stage_cache_enabled() if enabled is None else enabled
        if store is not None:
            self._store: Optional[WorkflowStore] = store
        elif self.enabled:
            self._store = WorkflowStore(STAGE_CACHE_DB_PATH)
        else:
            self._store = None

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
        instr = stable_short_hash(instructions, 16) if instructions else ""
        raw = "\x1f".join([agent_key, model or "", reasoning or "", instr, extra or "", canonical])
        return stable_short_hash(raw, 40)

    def lookup(self, key: str) -> Optional[dict[str, Any]]:
        """Return the cached stage output dump for ``key``, or None on a miss."""
        if not self.enabled or self._store is None or not key:
            return None
        record = self._store.load(_CACHE_KIND, key)
        if not isinstance(record, dict):
            return None
        output = record.get("output")
        return output if isinstance(output, dict) else None

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
        self._store.save(
            _CACHE_KIND,
            key,
            {
                "agent_key": agent_key,
                "model": model,
                "reasoning": reasoning,
                "output": output,
                "updated_at": datetime.now().isoformat(),
            },
        )

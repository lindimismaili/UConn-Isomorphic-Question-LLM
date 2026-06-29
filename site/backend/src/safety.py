"""Filesystem and artifact safety helpers for workflow runtime code."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

LATEX_SUFFIXES = {".tex"}
PDF_SUFFIXES = {".pdf"}


def utc_run_timestamp() -> str:
    """Return a sortable UTC timestamp with microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a `Z` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_stem(value: str, *, fallback: str = "item", max_length: int = 80) -> str:
    """Return a filesystem-safe filename stem."""
    cleaned = _SAFE_NAME_PATTERN.sub("-", (value or "").strip()).strip("-._")
    return (cleaned[:max_length].strip("-._") or fallback)[:max_length]


def safe_filename(
    value: str,
    *,
    fallback: str,
    allowed_suffixes: set[str] | None = None,
) -> str:
    """Return a safe basename, preserving only allowed file suffixes."""
    raw_name = Path(value or fallback).name
    stem = safe_stem(Path(raw_name).stem, fallback=Path(fallback).stem or "file")
    suffix = Path(raw_name).suffix.lower() or Path(fallback).suffix.lower()
    if allowed_suffixes is not None and suffix not in allowed_suffixes:
        suffix = Path(fallback).suffix.lower()
    return f"{stem}{suffix}"


def ensure_child_path(root: Path, relative_path: str | Path) -> Path:
    """Resolve a child path and reject traversal or absolute paths."""
    rel = Path(relative_path)
    if rel.is_absolute() or not rel.parts:
        raise ValueError("Invalid relative path")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in rel.parts):
        raise ValueError("Invalid relative path")
    resolved = (root / rel).resolve()
    resolved.relative_to(root.resolve())
    return resolved


def unique_child_name(directory: Path, filename: str, used_names: set[str] | None = None) -> str:
    """Return a unique filename for `directory`, preserving suffix."""
    used_names = used_names if used_names is not None else set()
    candidate = filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 2
    while candidate in used_names or (directory / candidate).exists():
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used_names.add(candidate)
    return candidate


def create_unique_run_dir(root: Path, workflow_id: str, *, suffix: str = "") -> tuple[Path, str]:
    """Create a unique workflow output directory and return `(path, label)`."""
    safe_id = safe_stem(workflow_id, fallback="workflow", max_length=64)
    suffix_part = f"-{safe_stem(suffix, fallback='run', max_length=32)}" if suffix else ""
    for attempt in range(20):
        label = f"{utc_run_timestamp()}-{safe_id}{suffix_part}"
        if attempt:
            label = f"{label}-{attempt}"
        path = root / label
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path, label
        except FileExistsError:
            continue
    raise FileExistsError("Unable to create a unique workflow output directory")


def stable_short_hash(value: str, length: int = 8) -> str:
    """Return a short stable hash for labels and manifest IDs."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


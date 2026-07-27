"""Filesystem and artifact safety helpers for workflow runtime code."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_OPERATION_PATTERN = re.compile(r"[^A-Za-z0-9 ._/-]+")

LATEX_SUFFIXES = {".tex"}
PDF_SUFFIXES = {".pdf"}
MAX_INSTRUCTION_CHARS = 20_000


def validate_instruction_text(value: str | None) -> str:
    """Return a canonical bounded instruction before cost or persistence.

    The same ceiling is enforced again by the input guardrail for direct engine
    callers. API and CLI boundaries call this first so an oversized form field
    is never fingerprinted, persisted, or allowed to trigger provider setup.
    """
    instruction = (value or "").strip()
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise ValueError(
            f"Instruction must be at most {MAX_INSTRUCTION_CHARS} characters."
        )
    return instruction


def safe_exception_category(exc: BaseException) -> str:
    """Return a bounded exception class name without rendering its message.

    Provider and model exceptions can embed raw model output, request data, or
    credentials in ``str(exc)``. Public state, durable artifacts, and routine
    logs should retain only this diagnostic category.
    """
    declared_category = getattr(exc, "safe_category", "")
    raw_category = (
        declared_category
        if isinstance(declared_category, str) and declared_category
        else type(exc).__name__
    )
    category = _SAFE_NAME_PATTERN.sub("", raw_category)[:80]
    return category or "Exception"


def safe_exception_message(exc: BaseException, *, operation: str) -> str:
    """Describe a failed operation without including exception-controlled text."""
    safe_operation = _SAFE_OPERATION_PATTERN.sub("", operation).strip()[:120]
    return f"{safe_operation or 'Operation'} failed ({safe_exception_category(exc)})"


class SanitizedProviderError(ValueError):
    """A provider failure whose message and retained category are safe to surface."""

    def __init__(self, *, operation: str, cause: BaseException) -> None:
        self.safe_category = safe_exception_category(cause)
        super().__init__(safe_exception_message(cause, operation=operation))


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


def local_run_segments() -> tuple[str, str]:
    """Return `(date, time)` folder segments for the current *local* time.

    The date uses `mm-dd-yyyy` and the time uses `hh-mm-ss` so the archive
    folders read like the wall-clock day/time the run happened on.
    """
    now = datetime.now()
    return now.strftime("%m-%d-%Y"), now.strftime("%H-%M-%S")


def create_unique_run_dir(root: Path, workflow_id: str, *, suffix: str = "") -> tuple[Path, str]:
    """Create a dated run directory and return `(path, label)`.

    Runs are grouped into a per-day folder and each run is a time-named
    subfolder, e.g. `root/06-29-2026/14-23-05`. When several runs land in the
    same second the subfolder gets a zero-padded numeric suffix (`-01`, `-02`,
    …) so nothing is ever overwritten. `label` is the posix-style path relative
    to `root` (e.g. `06-29-2026/14-23-05`); `workflow_id` is retained for the
    call signature but the folder name is derived from the time, not the id.
    """
    _ = workflow_id  # identity is recorded inside the run, not in the folder name
    date_dir, time_name = local_run_segments()
    suffix_part = f"-{safe_stem(suffix, fallback='run', max_length=32)}" if suffix else ""
    base_dir = root / date_dir
    for attempt in range(100):
        collision_part = f"-{attempt:02d}" if attempt else ""
        run_name = f"{time_name}{suffix_part}{collision_part}"
        path = base_dir / run_name
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path, f"{date_dir}/{run_name}"
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

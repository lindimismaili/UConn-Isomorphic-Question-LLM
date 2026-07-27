"""Fail-closed item limits and optional quality/cost tradeoffs."""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _bounded_int(
    name: str,
    *,
    default: int,
    maximum: int,
    minimum: int = 1,
) -> int:
    raw = (os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer from {minimum} to {maximum}"
        ) from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def max_source_questions() -> int:
    """Maximum extracted questions allowed into verifier/generator stages."""
    return _bounded_int("MAX_SOURCE_QUESTIONS", default=25, maximum=200)


def max_generated_questions() -> int:
    """Maximum generated questions allowed in one workflow."""
    return _bounded_int("MAX_GENERATED_QUESTIONS", default=100, maximum=500)


def max_evaluation_items() -> int:
    """Maximum generated questions submitted to the multi-grader evaluator."""
    return _bounded_int("MAX_EVALUATION_ITEMS", default=100, maximum=500)


def grader_max_completion_tokens() -> int:
    """Maximum tokens each individual score-model grader may generate."""
    return _bounded_int(
        "GRADER_MAX_COMPLETION_TOKENS",
        default=4096,
        minimum=256,
        maximum=16384,
    )


def max_eval_batch_entries() -> int:
    """Maximum prior runs or generated PDFs accepted by one eval batch."""
    return _bounded_int("MAX_EVAL_BATCH_ENTRIES", default=5, maximum=20)


def max_eval_batch_items() -> int:
    """Maximum total questions submitted across one eval batch."""
    return _bounded_int("MAX_EVAL_BATCH_ITEMS", default=100, maximum=500)


def max_create_data_entries() -> int:
    """Maximum fixture workflows admitted by one Create Data request."""
    return _bounded_int("MAX_CREATE_DATA_ENTRIES", default=5, maximum=25)


def max_active_batch_jobs() -> int:
    """Maximum active Create Data and eval-batch jobs combined."""
    return _bounded_int("MAX_ACTIVE_BATCH_JOBS", default=1, maximum=4)


def max_workflow_agent_requests() -> int:
    """Maximum pipeline-agent model turns across one workflow."""
    return _bounded_int(
        "MAX_WORKFLOW_AGENT_REQUESTS",
        default=32,
        maximum=100,
    )


def max_workflow_agent_tokens() -> int:
    """Maximum observed pipeline-agent tokens across one workflow."""
    return _bounded_int(
        "MAX_WORKFLOW_AGENT_TOKENS",
        default=750_000,
        maximum=5_000_000,
    )


def download_zip_max_source_bytes() -> int:
    """Maximum uncompressed run size accepted by the browser-download ZIP builder."""
    return _bounded_int(
        "DOWNLOAD_ZIP_MAX_SOURCE_BYTES",
        default=128 * 1024 * 1024,
        maximum=512 * 1024 * 1024,
    )


def generator_style_review_enabled() -> bool:
    """Whether to pay for a second full generator pass devoted to wording."""
    raw = (os.getenv("GENERATOR_STYLE_REVIEW_ENABLED", "false") or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ValueError(
        "GENERATOR_STYLE_REVIEW_ENABLED must be one of: "
        "1, 0, true, false, yes, no, on, off"
    )


def require_within_limit(label: str, count: int, maximum: int, env_name: str) -> None:
    """Raise before downstream paid calls when an item count exceeds policy."""
    if count > maximum:
        raise ValueError(
            f"{label} count {count} exceeds the configured limit {maximum} "
            f"({env_name}). Reduce the input or explicitly raise the limit."
        )

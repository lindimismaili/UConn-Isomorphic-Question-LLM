"""Hard per-run cost ceilings and optional generator review policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.src import cost_controls


def test_cost_control_defaults_are_bounded_for_professor_runs() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert cost_controls.max_source_questions() == 25
        assert cost_controls.max_generated_questions() == 100
        assert cost_controls.max_evaluation_items() == 100
        assert cost_controls.grader_max_completion_tokens() == 4096
        assert cost_controls.download_zip_max_source_bytes() == 128 * 1024 * 1024
        assert cost_controls.generator_style_review_enabled() is False


@pytest.mark.parametrize(
    ("name", "getter", "maximum"),
    [
        ("MAX_SOURCE_QUESTIONS", cost_controls.max_source_questions, 200),
        ("MAX_GENERATED_QUESTIONS", cost_controls.max_generated_questions, 500),
        ("MAX_EVALUATION_ITEMS", cost_controls.max_evaluation_items, 500),
        ("MAX_EVAL_BATCH_ENTRIES", cost_controls.max_eval_batch_entries, 20),
        ("MAX_EVAL_BATCH_ITEMS", cost_controls.max_eval_batch_items, 500),
        ("MAX_CREATE_DATA_ENTRIES", cost_controls.max_create_data_entries, 25),
        ("MAX_ACTIVE_BATCH_JOBS", cost_controls.max_active_batch_jobs, 4),
        (
            "DOWNLOAD_ZIP_MAX_SOURCE_BYTES",
            cost_controls.download_zip_max_source_bytes,
            512 * 1024 * 1024,
        ),
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_cost_limits_reject_unsafe_values(name, getter, maximum, value: str) -> None:
    with patch.dict("os.environ", {name: value}, clear=False):
        with pytest.raises(ValueError, match=f"1 to {maximum}"):
            getter()


@pytest.mark.parametrize(
    ("name", "getter", "maximum"),
    [
        ("MAX_SOURCE_QUESTIONS", cost_controls.max_source_questions, 200),
        ("MAX_GENERATED_QUESTIONS", cost_controls.max_generated_questions, 500),
        ("MAX_EVALUATION_ITEMS", cost_controls.max_evaluation_items, 500),
        ("MAX_EVAL_BATCH_ENTRIES", cost_controls.max_eval_batch_entries, 20),
        ("MAX_EVAL_BATCH_ITEMS", cost_controls.max_eval_batch_items, 500),
        ("MAX_CREATE_DATA_ENTRIES", cost_controls.max_create_data_entries, 25),
        ("MAX_ACTIVE_BATCH_JOBS", cost_controls.max_active_batch_jobs, 4),
        (
            "DOWNLOAD_ZIP_MAX_SOURCE_BYTES",
            cost_controls.download_zip_max_source_bytes,
            512 * 1024 * 1024,
        ),
    ],
)
def test_cost_limits_reject_values_above_hard_maximum(name, getter, maximum) -> None:
    with patch.dict("os.environ", {name: str(maximum + 1)}, clear=False):
        with pytest.raises(ValueError, match=f"1 to {maximum}"):
            getter()


def test_style_review_toggle_is_strict() -> None:
    with patch.dict("os.environ", {"GENERATOR_STYLE_REVIEW_ENABLED": "yes"}):
        assert cost_controls.generator_style_review_enabled() is True
    with patch.dict("os.environ", {"GENERATOR_STYLE_REVIEW_ENABLED": "sometimes"}):
        with pytest.raises(ValueError, match="must be one of"):
            cost_controls.generator_style_review_enabled()


@pytest.mark.parametrize("value", ["0", "255", "16385", "not-a-number"])
def test_grader_completion_token_limit_rejects_unsafe_values(value: str) -> None:
    with patch.dict(
        "os.environ",
        {"GRADER_MAX_COMPLETION_TOKENS": value},
        clear=False,
    ):
        with pytest.raises(ValueError, match="256 to 16384"):
            cost_controls.grader_max_completion_tokens()


def test_limit_error_names_remediation_setting() -> None:
    with pytest.raises(ValueError, match="MAX_SOURCE_QUESTIONS"):
        cost_controls.require_within_limit(
            "Source question",
            26,
            25,
            "MAX_SOURCE_QUESTIONS",
        )

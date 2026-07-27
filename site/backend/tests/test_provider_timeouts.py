"""Fail-closed configuration and cancellation tests for provider deadlines."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from backend.src.provider_timeouts import (
    ProviderDeadlineExceeded,
    agent_stage_timeout_seconds,
    await_cancellation_safe_blocking_call,
    await_with_deadline,
    container_task_timeout_seconds,
    evaluation_timeout_seconds,
    guardrail_max_classifier_requests,
    guardrail_stage_timeout_seconds,
    guardrail_timeout_seconds,
    openai_transport_options,
    provider_max_retries,
    provider_request_timeout_seconds,
    validate_evaluation_wait_seconds,
)


def test_provider_deadline_defaults_are_finite() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert agent_stage_timeout_seconds() == 900
        assert guardrail_timeout_seconds() == 120
        assert guardrail_stage_timeout_seconds() == 300
        assert guardrail_max_classifier_requests() == 16
        assert container_task_timeout_seconds() == 600
        assert evaluation_timeout_seconds() == 900
        assert provider_request_timeout_seconds() == 120
        assert provider_max_retries() == 0
        assert openai_transport_options() == {"timeout": 120, "max_retries": 0}


@pytest.mark.parametrize(
    ("name", "loader", "invalid"),
    [
        ("OPENAI_AGENT_STAGE_TIMEOUT_SECONDS", agent_stage_timeout_seconds, "29"),
        ("OPENAI_GUARDRAIL_TIMEOUT_SECONDS", guardrail_timeout_seconds, "nan"),
        (
            "OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS",
            guardrail_stage_timeout_seconds,
            "1801",
        ),
        (
            "OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS",
            guardrail_max_classifier_requests,
            "0",
        ),
        ("OPENAI_CONTAINER_TASK_TIMEOUT_SECONDS", container_task_timeout_seconds, "1801"),
        ("OPENAI_EVALUATION_TIMEOUT_SECONDS", evaluation_timeout_seconds, "disabled"),
        (
            "OPENAI_PROVIDER_REQUEST_TIMEOUT_SECONDS",
            provider_request_timeout_seconds,
            "601",
        ),
        ("OPENAI_PROVIDER_MAX_RETRIES", provider_max_retries, "-1"),
    ],
)
def test_provider_deadline_config_rejects_unsafe_values(
    name: str,
    loader,
    invalid: str,
) -> None:
    with patch.dict("os.environ", {name: invalid}):
        with pytest.raises(ValueError, match=name):
            loader()


@pytest.mark.parametrize("value", [-1, float("nan"), 3601, "not-a-number"])
def test_evaluation_explicit_wait_override_is_finite_and_bounded(value) -> None:
    with pytest.raises(ValueError, match="max_wait_seconds"):
        validate_evaluation_wait_seconds(value)


def test_zero_evaluation_wait_is_reserved_for_offline_test_seam() -> None:
    assert validate_evaluation_wait_seconds(0) == 0


def test_await_with_deadline_preserves_external_cancellation() -> None:
    async def _exercise() -> None:
        started = asyncio.Event()

        async def _wait_forever() -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            await_with_deadline(
                _wait_forever(),
                operation="test operation",
                timeout_seconds=30,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_exercise())


def test_await_with_deadline_uses_safe_deterministic_error() -> None:
    async def _exercise() -> None:
        with pytest.raises(ProviderDeadlineExceeded, match="test operation") as exc_info:
            await await_with_deadline(
                asyncio.Event().wait(),
                operation="test operation",
                timeout_seconds=0.01,
            )
        assert exc_info.value.safe_category == "ProviderDeadlineExceeded"

    asyncio.run(_exercise())


def test_cancellation_safe_blocking_call_drains_worker_before_cancelling() -> None:
    async def _exercise() -> list[str]:
        started = asyncio.Event()
        release = asyncio.Event()
        events: list[str] = []

        async def delayed_to_thread(operation, /, *args, **kwargs):
            started.set()
            await release.wait()
            events.append("worker-finished")
            return operation(*args, **kwargs)

        task = asyncio.create_task(
            await_cancellation_safe_blocking_call(
                delayed_to_thread,
                lambda: "result",
            )
        )
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        events.append("caller-cancelled")
        return events

    assert asyncio.run(_exercise()) == [
        "worker-finished",
        "caller-cancelled",
    ]

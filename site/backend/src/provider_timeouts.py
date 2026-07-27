"""Bounded provider-operation deadlines shared by CLI and server workflows."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

_T = TypeVar("_T")


def _bounded_seconds(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = (os.getenv(name, str(default)) or "").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a number from {minimum:g} to {maximum:g}"
        ) from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a number from {minimum:g} to {maximum:g}")
    return value


def agent_stage_timeout_seconds() -> float:
    """Maximum wall-clock time for one Agents SDK stage attempt."""
    return _bounded_seconds(
        "OPENAI_AGENT_STAGE_TIMEOUT_SECONDS",
        default=900,
        minimum=30,
        maximum=3600,
    )


def guardrail_timeout_seconds() -> float:
    """Maximum wall-clock time for one moderation or classifier request."""
    return _bounded_seconds(
        "OPENAI_GUARDRAIL_TIMEOUT_SECONDS",
        default=120,
        minimum=10,
        maximum=600,
    )


def guardrail_stage_timeout_seconds() -> float:
    """Maximum wall-clock time for one complete guardrail classifier phase."""
    return _bounded_seconds(
        "OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS",
        default=300,
        minimum=10,
        maximum=1800,
    )


def guardrail_max_classifier_requests() -> int:
    """Maximum classifier calls across one input or content guardrail phase."""
    name = "OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS"
    raw = (os.getenv(name, "16") or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer from 1 to 16") from exc
    if not 1 <= value <= 16:
        raise ValueError(f"{name} must be an integer from 1 to 16")
    return value


def container_task_timeout_seconds() -> float:
    """Maximum wall-clock time for one container shell/tool operation."""
    return _bounded_seconds(
        "OPENAI_CONTAINER_TASK_TIMEOUT_SECONDS",
        default=600,
        minimum=30,
        maximum=1800,
    )


def evaluation_timeout_seconds() -> float:
    """Maximum time an OpenAI Evals run may remain non-terminal."""
    return _bounded_seconds(
        "OPENAI_EVALUATION_TIMEOUT_SECONDS",
        default=900,
        minimum=60,
        maximum=3600,
    )


def provider_request_timeout_seconds() -> float:
    """Finite SDK transport timeout for synchronous provider requests."""
    return _bounded_seconds(
        "OPENAI_PROVIDER_REQUEST_TIMEOUT_SECONDS",
        default=120,
        minimum=10,
        maximum=600,
    )


def provider_max_retries() -> int:
    """Bound SDK retries; zero avoids duplicate paid/resource-creation calls."""
    raw = (os.getenv("OPENAI_PROVIDER_MAX_RETRIES", "0") or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("OPENAI_PROVIDER_MAX_RETRIES must be an integer from 0 to 2") from exc
    if not 0 <= value <= 2:
        raise ValueError("OPENAI_PROVIDER_MAX_RETRIES must be an integer from 0 to 2")
    return value


def openai_transport_options() -> dict[str, float | int]:
    """Options applied to every explicitly constructed synchronous OpenAI client."""
    return {
        "timeout": provider_request_timeout_seconds(),
        "max_retries": provider_max_retries(),
    }


def validate_evaluation_wait_seconds(value: float) -> float:
    """Validate the internal evaluation wait override used by offline tests."""
    try:
        validated = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_wait_seconds must be a finite number from 0 to 3600") from exc
    if not math.isfinite(validated) or not 0 <= validated <= 3600:
        raise ValueError("max_wait_seconds must be a finite number from 0 to 3600")
    return validated


class ProviderDeadlineExceeded(ValueError):
    """Safe, deterministic error raised when a provider operation times out."""

    safe_category = "ProviderDeadlineExceeded"

    def __init__(self, *, operation: str, timeout_seconds: float) -> None:
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        seconds = f"{timeout_seconds:g}"
        super().__init__(
            f"{operation} exceeded the configured {seconds}-second provider deadline"
        )


async def await_with_deadline(
    awaitable: Awaitable[_T],
    *,
    operation: str,
    timeout_seconds: float,
) -> _T:
    """Await cooperatively with a finite timeout while preserving cancellation."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 3600
    ):
        raise ValueError("timeout_seconds must be a finite number greater than 0 and at most 3600")
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise ProviderDeadlineExceeded(
            operation=operation,
            timeout_seconds=timeout_seconds,
        ) from None


async def await_cancellation_safe_blocking_call(
    to_thread: Callable[..., Awaitable[_T]],
    operation: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Drain a blocking worker before propagating caller cancellation.

    Python cannot stop an already-running worker thread. Shielding and draining
    prevents the owning workflow task from unregistering, closing its provider
    client, deleting a container, or exposing mutable artifacts while that
    worker still performs provider or filesystem side effects.
    """
    task = asyncio.create_task(to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        await drain_task_outcome(task)
        raise cancellation


async def drain_task_outcome(
    task: asyncio.Task[_T],
) -> _T | BaseException:
    """Wait until ``task`` is done despite repeated caller cancellation.

    This primitive is for ownership boundaries where returning early would lose
    a newly created provider ID, race resource teardown, or leave a worker
    mutating files after its workflow has unregistered. The caller decides how
    to interpret the returned result/exception and whether to re-raise its
    original cancellation.
    """
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            # An inner failure also marks the task done. Read it synchronously
            # below so no further cancellation point exists after completion.
            continue
    try:
        return task.result()
    except BaseException as exc:
        return exc

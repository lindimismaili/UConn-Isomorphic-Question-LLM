"""Lease helpers that serialize artifact access with cleanup and maintenance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from fastapi import HTTPException

from backend.src.deployment_maintenance import (
    DeploymentMaintenanceError,
    PaidWorkLease,
    acquire_paid_work_lease,
    try_acquire_artifact_mutation_lease,
)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def acquire_artifact_access_lease() -> PaidWorkLease:
    """Acquire a shared lease or map maintenance refusal to HTTP 503."""
    try:
        return acquire_paid_work_lease()
    except DeploymentMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def acquire_artifact_mutation_lease() -> PaidWorkLease:
    """Acquire the exclusive artifact writer lease without waiting."""
    try:
        lease = try_acquire_artifact_mutation_lease()
    except DeploymentMaintenanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if lease is None:
        raise HTTPException(
            status_code=409,
            detail="Artifacts are currently in use; retry after active work finishes.",
        )
    return lease


def artifact_access_endpoint(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Hold a shared artifact lease for the complete endpoint coroutine."""

    @wraps(func)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        lease = acquire_artifact_access_lease()
        try:
            return await func(*args, **kwargs)
        finally:
            lease.close()

    return wrapped


def artifact_mutation_endpoint(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Hold the exclusive artifact lease for one endpoint writer."""

    @wraps(func)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        lease = acquire_artifact_mutation_lease()
        try:
            return await func(*args, **kwargs)
        finally:
            lease.close()

    return wrapped


def close_artifact_lease(lease: PaidWorkLease) -> None:
    """Starlette background callback used after a FileResponse finishes."""
    lease.close()


__all__ = [
    "acquire_artifact_access_lease",
    "acquire_artifact_mutation_lease",
    "artifact_access_endpoint",
    "artifact_mutation_endpoint",
    "close_artifact_lease",
]

"""Durable duplicate suppression for cost-bearing start requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.src.provider_timeouts import drain_task_outcome
from backend.src.workflow.store import WorkflowStore

IDEMPOTENCY_HEADER = "X-Idempotency-Key"
MIN_IDEMPOTENCY_KEY_LENGTH = 16
MAX_IDEMPOTENCY_KEY_LENGTH = 128
_IDEMPOTENCY_KEY_PATTERN = re.compile(
    rf"[A-Za-z0-9._~:-]{{{MIN_IDEMPOTENCY_KEY_LENGTH},{MAX_IDEMPOTENCY_KEY_LENGTH}}}"
)
_PENDING_WAIT_SECONDS = 5.0
_PENDING_POLL_SECONDS = 0.025
_KEY_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_KEY_LOCKS_GUARD = threading.Lock()


def validate_idempotency_key(value: str | None) -> str:
    """Return a canonical client key or reject an unsafe/missing header."""
    key = (value or "").strip()
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{IDEMPOTENCY_HEADER} is required and must be "
                f"{MIN_IDEMPOTENCY_KEY_LENGTH}-{MAX_IDEMPOTENCY_KEY_LENGTH} "
                "characters using only letters, numbers, and . _ ~ : -"
            ),
        )
    return key


def paid_start_key_lock(value: str | None) -> tuple[str, asyncio.Lock]:
    """Return the process-local serialization lock for one validated key."""
    key = validate_idempotency_key(value)
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _KEY_LOCKS[key] = lock
    return key, lock


def request_fingerprint(payload: Any) -> str:
    """Hash one canonical request description without persisting its contents."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bytes_fingerprint(content: bytes) -> str:
    """Return a stable digest for an uploaded request body."""
    return hashlib.sha256(content).hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Return a content-bound identity for a local request input."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {
        "name": path.name,
        "size": size,
        "sha256": digest.hexdigest(),
    }


@dataclass(frozen=True)
class IdempotencyDecision:
    """The durable ownership decision for one paid start."""

    owner: bool
    key: str
    endpoint: str
    fingerprint: str
    resource_kind: str
    resource_id: str
    response: dict[str, Any]


def _validate_existing(
    record: dict[str, Any],
    *,
    endpoint: str,
    fingerprint: str,
) -> None:
    if (
        record.get("endpoint") != endpoint
        or record.get("fingerprint") != fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{IDEMPOTENCY_HEADER} was already used for a different "
                "endpoint or request payload"
            ),
        )


async def _require_replay_resource(
    store: WorkflowStore,
    *,
    resource_kind: str,
    resource_id: str,
) -> None:
    """Reject a committed replay after its referenced resource has expired.

    The idempotency ledger intentionally outlives workflow and batch records so
    an old request key cannot silently start paid work again.  Returning the
    stored success payload after the resource itself has been pruned would,
    however, hand the caller an unusable id that immediately 404s.
    """
    resource = None
    if resource_kind and resource_id:
        resource = await asyncio.to_thread(
            store.load,
            resource_kind,
            resource_id,
        )
    if resource is None:
        raise HTTPException(
            status_code=410,
            detail=(
                "The paid start associated with this idempotency key has "
                "expired; submit the request with a new idempotency key."
            ),
        )


async def _claim_once(
    store: WorkflowStore,
    *,
    key: str,
    endpoint: str,
    fingerprint: str,
    resource_kind: str,
    resource_id: str,
    response: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Drain an atomic claim and release it if its caller is cancelled."""
    task = asyncio.create_task(
        asyncio.to_thread(
            store.claim_idempotency,
            key=key,
            endpoint=endpoint,
            fingerprint=fingerprint,
            resource_kind=resource_kind,
            resource_id=resource_id,
            response=response,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        outcome = await drain_task_outcome(task)
        if (
            isinstance(outcome, tuple)
            and len(outcome) == 2
            and outcome[0] is True
        ):
            cleanup = asyncio.create_task(
                asyncio.to_thread(
                    store.abandon_idempotency,
                    key=key,
                    endpoint=endpoint,
                    fingerprint=fingerprint,
                    resource_id=resource_id,
                )
            )
            await drain_task_outcome(cleanup)
        raise cancellation


async def claim_paid_start(
    store: WorkflowStore,
    *,
    raw_key: str | None,
    endpoint: str,
    fingerprint: str,
    resource_kind: str,
    resource_id: str,
    response: dict[str, Any],
) -> IdempotencyDecision:
    """Claim a start exactly once, waiting briefly for a concurrent owner."""
    key = validate_idempotency_key(raw_key)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PENDING_WAIT_SECONDS

    while True:
        owner, record = await _claim_once(
            store,
            key=key,
            endpoint=endpoint,
            fingerprint=fingerprint,
            resource_kind=resource_kind,
            resource_id=resource_id,
            response=response,
        )
        if owner:
            return IdempotencyDecision(
                owner=True,
                key=key,
                endpoint=endpoint,
                fingerprint=fingerprint,
                resource_kind=resource_kind,
                resource_id=resource_id,
                response=response,
            )

        _validate_existing(record, endpoint=endpoint, fingerprint=fingerprint)
        existing_response = record.get("response")
        if not isinstance(existing_response, dict):
            raise HTTPException(
                status_code=500,
                detail="Stored idempotency response is invalid",
            )
        existing_resource_kind = str(record.get("resource_kind") or "")
        existing_resource_id = str(record.get("resource_id") or "")
        if record.get("status") == "committed":
            await _require_replay_resource(
                store,
                resource_kind=existing_resource_kind,
                resource_id=existing_resource_id,
            )
            return IdempotencyDecision(
                owner=False,
                key=key,
                endpoint=endpoint,
                fingerprint=fingerprint,
                resource_kind=existing_resource_kind,
                resource_id=existing_resource_id,
                response=existing_response,
            )

        # The paid resource record is persisted before its task is scheduled.
        # Seeing it is sufficient to close a crash/restart window even if the
        # original request never reached the response write.
        resource = await asyncio.to_thread(
            store.load,
            existing_resource_kind,
            existing_resource_id,
        )
        if resource is not None:
            await asyncio.to_thread(
                store.commit_idempotency,
                key=key,
                endpoint=endpoint,
                fingerprint=fingerprint,
                resource_id=existing_resource_id,
            )
            return IdempotencyDecision(
                owner=False,
                key=key,
                endpoint=endpoint,
                fingerprint=fingerprint,
                resource_kind=existing_resource_kind,
                resource_id=existing_resource_id,
                response=existing_response,
            )

        if loop.time() >= deadline:
            raise HTTPException(
                status_code=425,
                detail="An identical paid start request is still being accepted",
                headers={"Retry-After": "1"},
            )
        await asyncio.sleep(_PENDING_POLL_SECONDS)
        refreshed = await asyncio.to_thread(store.load_idempotency, key)
        if refreshed is None:
            # The original request failed before admitting any paid resource.
            # Compete for ownership again using the same client key.
            continue
        _validate_existing(refreshed, endpoint=endpoint, fingerprint=fingerprint)
        if refreshed.get("status") == "committed":
            refreshed_response = refreshed.get("response")
            if not isinstance(refreshed_response, dict):
                raise HTTPException(
                    status_code=500,
                    detail="Stored idempotency response is invalid",
                )
            refreshed_resource_kind = str(
                refreshed.get("resource_kind") or ""
            )
            refreshed_resource_id = str(refreshed.get("resource_id") or "")
            await _require_replay_resource(
                store,
                resource_kind=refreshed_resource_kind,
                resource_id=refreshed_resource_id,
            )
            return IdempotencyDecision(
                owner=False,
                key=key,
                endpoint=endpoint,
                fingerprint=fingerprint,
                resource_kind=refreshed_resource_kind,
                resource_id=refreshed_resource_id,
                response=refreshed_response,
            )


async def commit_paid_start(
    store: WorkflowStore,
    decision: IdempotencyDecision,
) -> None:
    """Commit an owner claim after its task has been registered."""
    if not decision.owner:
        return
    await asyncio.to_thread(
        store.commit_idempotency,
        key=decision.key,
        endpoint=decision.endpoint,
        fingerprint=decision.fingerprint,
        resource_id=decision.resource_id,
    )


async def abandon_paid_start(
    store: WorkflowStore,
    decision: IdempotencyDecision | None,
) -> None:
    """Release an owner claim only when no paid resource was scheduled."""
    if decision is None or not decision.owner:
        return
    await asyncio.to_thread(
        store.abandon_idempotency,
        key=decision.key,
        endpoint=decision.endpoint,
        fingerprint=decision.fingerprint,
        resource_id=decision.resource_id,
    )

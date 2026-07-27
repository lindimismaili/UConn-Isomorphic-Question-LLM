from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.src.workflow.stage_cache import StageCache
from backend.src.workflow.store import WorkflowStore


def _cache(tmp_path: Path, **kwargs: object) -> tuple[StageCache, WorkflowStore]:
    store = WorkflowStore(tmp_path / "cache.sqlite3")
    return StageCache(enabled=True, store=store, **kwargs), store


def _record_count(store: WorkflowStore) -> int:
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM records WHERE kind = ?",
            ("stage_cache",),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _replace_raw_payload(store: WorkflowStore, key: str, payload: str) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE records SET payload = ? WHERE kind = ? AND id = ?",
            (payload, "stage_cache", key),
        )


def test_key_is_versioned_and_covers_invocation_inputs(tmp_path: Path) -> None:
    cache, _ = _cache(tmp_path)

    key = cache.make_key(
        "generator",
        {"questions": [1, 2]},
        model="gpt-test",
        reasoning="low",
        instructions="Generate carefully",
        extra="source-hash",
    )

    assert key.startswith("stage-cache-v2:")
    assert key == cache.make_key(
        "generator",
        {"questions": [1, 2]},
        model="gpt-test",
        reasoning="low",
        instructions="Generate carefully",
        extra="source-hash",
    )
    assert key != cache.make_key(
        "generator",
        {"questions": [1, 2]},
        model="gpt-test",
        reasoning="high",
        instructions="Generate carefully",
        extra="source-hash",
    )


def test_store_round_trip_includes_expiring_versioned_record(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, ttl_seconds=300)
    key = cache.make_key("extracter", {"file": "hash"})

    cache.store(
        key,
        agent_key="extracter",
        output={"source_questions": [{"stem": "Example"}]},
        model="gpt-test",
        reasoning="none",
    )

    assert cache.lookup(key) == {"source_questions": [{"stem": "Example"}]}
    record = store.load("stage_cache", key)
    assert record is not None
    assert record["schema_version"] == 2
    assert record["key_version"] == "stage-cache-v2"
    assert record["cache_key"] == key
    assert record["expires_at_epoch"] > record["updated_at_epoch"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: {**record, "schema_version": 1},
        lambda record: {**record, "cache_key": "different-key"},
        lambda record: {**record, "expires_at_epoch": 0},
        lambda record: {**record, "output": ["not", "an", "object"]},
    ],
)
def test_invalid_or_expired_record_is_a_miss_and_is_evicted(
    tmp_path: Path,
    mutate,
) -> None:
    cache, store = _cache(tmp_path)
    key = cache.make_key("generator", {"input": 1})
    cache.store(key, agent_key="generator", output={"generated_questions": []})
    record = store.load("stage_cache", key)
    assert record is not None
    store.save("stage_cache", key, mutate(record))

    assert cache.lookup(key) is None
    assert cache.lookup(key) is None
    assert _record_count(store) == 0


def test_malformed_json_record_is_a_miss_and_is_evicted(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    key = cache.make_key("generator", {"input": 1})
    cache.store(key, agent_key="generator", output={"generated_questions": []})
    _replace_raw_payload(store, key, "{not valid JSON")

    assert cache.lookup(key) is None
    assert _record_count(store) == 0


def test_entry_cap_evicts_oldest_records(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_entries=2)
    keys: list[str] = []

    for value in range(3):
        key = cache.make_key("generator", {"input": value})
        keys.append(key)
        cache.store(key, agent_key="generator", output={"value": value})

    assert _record_count(store) == 2
    assert cache.lookup(keys[0]) is None
    assert cache.lookup(keys[1]) == {"value": 1}
    assert cache.lookup(keys[2]) == {"value": 2}


def test_tightening_ttl_evicts_records_created_under_a_longer_limit(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, ttl_seconds=300)
    key = cache.make_key("generator", {"input": 1})
    cache.store(key, agent_key="generator", output={"value": 1})
    record = store.load("stage_cache", key)
    assert record is not None
    record["updated_at_epoch"] = time.time() - 100
    record["expires_at_epoch"] = time.time() + 200
    store.save("stage_cache", key, record)

    tightened = StageCache(enabled=True, store=store, ttl_seconds=50)

    assert tightened.lookup(key) is None
    assert _record_count(store) == 0


def test_oversized_record_is_not_retained(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_record_bytes=512)
    key = cache.make_key("extracter", {"input": 1})
    cache.store(key, agent_key="extracter", output={"text": "x" * 2_000})

    assert cache.lookup(key) is None
    assert _record_count(store) == 0


def test_unserializable_output_does_not_abort_and_evicts_stale_value(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path)
    key = cache.make_key("generator", {"input": 1})
    cache.store(key, agent_key="generator", output={"value": "stale"})
    circular: dict[str, object] = {}
    circular["self"] = circular

    cache.store(key, agent_key="generator", output=circular)

    assert cache.lookup(key) is None
    assert _record_count(store) == 0


def test_concurrent_writers_use_atomic_bounded_transactions(tmp_path: Path) -> None:
    cache, store = _cache(tmp_path, max_entries=8)

    def write(value: int) -> None:
        key = cache.make_key("generator", {"input": value})
        cache.store(key, agent_key="generator", output={"value": value})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(32)))

    records = store.list_records("stage_cache")
    assert len(records) == 8
    assert all(cache.lookup(key) == record["output"] for key, record in records)


def test_environment_limits_are_finite_and_invalid_values_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAGE_CACHE_TTL_SECONDS", "unlimited")
    monkeypatch.setenv("STAGE_CACHE_MAX_ENTRIES", "-1")
    monkeypatch.setenv("STAGE_CACHE_MAX_RECORD_BYTES", "999999999")

    cache, _ = _cache(tmp_path)

    assert cache.ttl_seconds == 24 * 60 * 60
    assert cache.max_entries == 128
    assert cache.max_record_bytes == 16 * 1024 * 1024


def test_disabled_cache_does_not_create_or_retain_records(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "cache.sqlite3")
    cache = StageCache(enabled=False, store=store)
    key = cache.make_key("generator", {"input": 1})

    cache.store(key, agent_key="generator", output={"value": 1})

    assert cache.lookup(key) is None
    assert store.list_records("stage_cache") == []


def test_constructor_rejects_non_integer_or_non_positive_limits(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ValueError, match="positive integers"):
        StageCache(enabled=True, store=store, ttl_seconds=0)
    with pytest.raises(ValueError, match="positive integers"):
        StageCache(enabled=True, store=store, max_entries=1.5)  # type: ignore[arg-type]


def test_legacy_and_corrupt_records_are_removed_during_initial_prune(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "cache.sqlite3")
    store.save(
        "stage_cache",
        "legacy-key",
        {
            "agent_key": "generator",
            "output": {"private": "old cache"},
            "updated_at": "2026-01-01T00:00:00",
        },
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO records (kind, id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("stage_cache", "corrupt-key", json.dumps(["not", "an", "object"]), ""),
        )

    StageCache(enabled=True, store=store)

    assert store.list_records("stage_cache") == []

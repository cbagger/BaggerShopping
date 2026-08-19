from __future__ import annotations

import time
from typing import Any

from . import control_center_ops as ops


_STORAGE_CACHE_TTL_SECONDS = 60.0
_storage_cache: dict[str, Any] | None = None
_storage_cached_at = 0.0
_state_cache: dict[str, Any] | None = None

_base_storage_status = ops.storage_status


def storage_status(*, force: bool = False) -> dict[str, Any]:
    """Measure Kurv's directory footprint at most once per minute.

    Runtime/API state can refresh every few seconds; recursively walking `/data`
    cannot. Storage is capacity telemetry, so a one-minute cache is both more
    truthful and materially lighter on QNAP filesystem metadata.
    """
    global _storage_cache, _storage_cached_at
    now = time.monotonic()
    if not force and _storage_cache is not None and now - _storage_cached_at < _STORAGE_CACHE_TTL_SECONDS:
        return dict(_storage_cache)
    value = _base_storage_status()
    _storage_cache = dict(value)
    _storage_cached_at = now
    return value


def _load_state_once() -> dict[str, Any]:
    global _state_cache
    if _state_cache is None:
        loaded = ops._read_json(ops.STATE_PATH, {})
        _state_cache = loaded if isinstance(loaded, dict) else {}
    return dict(_state_cache)


def _runtime_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {"state": row.get("state"), "health": row.get("health")}
        for name, row in snapshot.get("runtime", {}).items()
        if isinstance(row, dict)
    }


def _coverage_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        str(row.get("publication_id")): row.get("coverage_status")
        for row in snapshot.get("flyers", {}).get("publications", [])
        if isinstance(row, dict)
    }


def record_snapshot(snapshot: dict[str, Any]) -> None:
    """Record only transitions or 5-minute trend samples.

    The live SSE cadence must never translate into continuous disk writes.
    """
    global _state_cache
    try:
        previous = _load_state_once()
        ops._record_transition_events(snapshot, previous)

        now = int(snapshot.get("generated_at") or time.time())
        last_sample = int(previous.get("last_sample_at") or 0)
        sample_due = now - last_sample >= ops.HISTORY_SAMPLE_SECONDS
        if sample_due:
            ops._append_jsonl(ops.HISTORY_PATH, ops._history_metric(snapshot), keep=ops.MAX_HISTORY)
            last_sample = now

        runtime = _runtime_state(snapshot)
        coverage = _coverage_state(snapshot)
        changed = runtime != previous.get("runtime", {}) or coverage != previous.get("coverage", {})
        next_state = {
            "last_sample_at": last_sample,
            "runtime": runtime,
            "coverage": coverage,
        }
        _state_cache = next_state
        if changed or sample_due or not previous:
            ops._write_json(ops.STATE_PATH, next_state)
    except Exception:
        return


def reset_caches_for_tests() -> None:
    global _storage_cache, _storage_cached_at, _state_cache
    _storage_cache = None
    _storage_cached_at = 0.0
    _state_cache = None

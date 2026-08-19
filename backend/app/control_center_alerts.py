from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import control_center_ops as ops


_store_cache: dict[str, Any] | None = None
_store_cache_path: Path | None = None


def _key(row: dict[str, Any]) -> str:
    return "|".join((str(row.get("severity") or ""), str(row.get("title") or ""), str(row.get("detail") or "")))[:1200]


def _load_store() -> dict[str, Any]:
    global _store_cache, _store_cache_path
    path = Path(ops.ALERTS_PATH)
    if _store_cache is None or _store_cache_path != path:
        loaded = ops._read_json(path, {"active": {}, "resolved": [], "episode_counts": {}})
        _store_cache = loaded if isinstance(loaded, dict) else {"active": {}, "resolved": [], "episode_counts": {}}
        _store_cache_path = path
    return _store_cache


def reconcile_alerts(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Track alert episodes without turning the SSE refresh into disk writes."""
    global _store_cache
    now = int(time.time())
    store = _load_store()
    previous = store.get("active", {}) if isinstance(store.get("active"), dict) else {}
    resolved = list(store.get("resolved", [])) if isinstance(store.get("resolved"), list) else []
    episode_counts = dict(store.get("episode_counts", {})) if isinstance(store.get("episode_counts"), dict) else {}

    next_active: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    current_keys = {_key(row) for row in active}
    previous_keys = set(previous)
    transition = current_keys != previous_keys

    for row in active:
        key = _key(row)
        old = previous.get(key) if isinstance(previous.get(key), dict) else None
        if old:
            first_seen = int(old.get("first_seen") or now)
            occurrences = int(old.get("occurrences") or episode_counts.get(key) or 1)
        else:
            first_seen = now
            occurrences = int(episode_counts.get(key) or 0) + 1
            episode_counts[key] = occurrences
            ops.append_event(
                category="system",
                event_type="alert_opened",
                title=str(row.get("title") or "Ny alarm"),
                detail=str(row.get("detail") or "") or None,
                severity="error" if row.get("severity") == "critical" else "warning",
                at=now,
            )
        life = {
            "first_seen": first_seen,
            "last_seen": now,
            "duration_seconds": max(0, now - first_seen),
            "occurrences": occurrences,
        }
        next_active[key] = life
        enriched.append({**row, **life})

    for key, old in previous.items():
        if key in current_keys or not isinstance(old, dict):
            continue
        title = key.split("|", 2)[1] if "|" in key else key
        resolved.append({
            "key": key,
            "title": title,
            "first_seen": old.get("first_seen"),
            "resolved_at": now,
            "duration_seconds": max(0, now - int(old.get("first_seen") or now)),
            "occurrences": old.get("occurrences"),
        })
        ops.append_event(category="system", event_type="alert_resolved", title=f"Løst · {title}", severity="success", at=now)

    next_store = {"active": next_active, "resolved": resolved[-80:], "episode_counts": episode_counts}
    _store_cache = next_store
    if transition or not store:
        ops._write_json(ops.ALERTS_PATH, next_store)
    return enriched


def reset_cache_for_tests() -> None:
    global _store_cache, _store_cache_path
    _store_cache = None
    _store_cache_path = None

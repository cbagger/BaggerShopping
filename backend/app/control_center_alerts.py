from __future__ import annotations

import time
from typing import Any

from . import control_center_ops as ops


def _key(row: dict[str, Any]) -> str:
    return "|".join((str(row.get("severity") or ""), str(row.get("title") or ""), str(row.get("detail") or "")))[:1200]


def reconcile_alerts(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Track continuous alert episodes, not snapshot/heartbeat frequency."""
    now = int(time.time())
    store = ops._read_json(ops.ALERTS_PATH, {"active": {}, "resolved": [], "episode_counts": {}})
    if not isinstance(store, dict):
        store = {"active": {}, "resolved": [], "episode_counts": {}}
    previous = store.get("active", {}) if isinstance(store.get("active"), dict) else {}
    resolved = store.get("resolved", []) if isinstance(store.get("resolved"), list) else []
    episode_counts = store.get("episode_counts", {}) if isinstance(store.get("episode_counts"), dict) else {}

    next_active: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    current_keys = {_key(row) for row in active}

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

    ops._write_json(
        ops.ALERTS_PATH,
        {"active": next_active, "resolved": resolved[-80:], "episode_counts": episode_counts},
    )
    return enriched

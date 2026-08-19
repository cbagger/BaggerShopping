from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


STORE_VERSION = 1


def telemetry_dir() -> Path:
    return Path(os.getenv("KURV_TELEMETRY_DIR", "/data/control-center/heartbeats"))


def _path(component: str) -> Path:
    safe = "".join(ch for ch in component.casefold() if ch.isalnum() or ch in {"-", "_"})
    return telemetry_dir() / f"{safe or 'unknown'}.json"


def write_heartbeat(
    component: str,
    *,
    status: str = "running",
    detail: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Best-effort, atomic worker telemetry.

    Observability must never be allowed to break a production worker. Any I/O
    error is therefore deliberately swallowed by callers through this function.
    """
    try:
        path = _path(component)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        payload = {
            "version": STORE_VERSION,
            "component": component,
            "status": str(status),
            "detail": str(detail)[:500] if detail else None,
            "metrics": metrics if isinstance(metrics, dict) else {},
            "updated_at": now,
        }
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "utf-8",
        )
        temporary.replace(path)
    except Exception:
        return


def read_heartbeat(component: str, *, stale_after: int = 120) -> dict[str, Any]:
    path = _path(component)
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "component": component,
            "status": "unknown",
            "detail": "Ingen heartbeat registreret endnu",
            "metrics": {},
            "updated_at": None,
            "age_seconds": None,
            "stale": True,
        }
    if not isinstance(payload, dict):
        payload = {}
    updated_at = int(payload.get("updated_at") or 0)
    age = max(0, int(time.time()) - updated_at) if updated_at else None
    stale = age is None or age > max(1, int(stale_after))
    status = str(payload.get("status") or "unknown")
    if stale and status not in {"error", "stopped"}:
        status = "stale"
    return {
        "component": component,
        "status": status,
        "detail": payload.get("detail"),
        "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        "updated_at": updated_at or None,
        "age_seconds": age,
        "stale": stale,
    }


def all_heartbeats() -> dict[str, dict[str, Any]]:
    path = telemetry_dir()
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in path.glob("*.json"):
        try:
            payload = json.loads(item.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        component = str(payload.get("component") or item.stem)
        result[component] = read_heartbeat(component)
    return result

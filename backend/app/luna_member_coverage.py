from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


COVERAGE_CONTRACT_VERSION = "member-price-full-flyer-v1"
STORE_VERSION = 1
_TERMINAL = {"complete", "degraded"}


def _store_path() -> Path:
    return Path(os.getenv("LUNA_MEMBER_COVERAGE_PATH", "/data/luna-member-coverage.json"))


def _empty_store() -> dict[str, Any]:
    return {"version": STORE_VERSION, "contract": COVERAGE_CONTRACT_VERSION, "items": {}}


def _load() -> dict[str, Any]:
    path = _store_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(value, dict):
        return _empty_store()
    if value.get("version") != STORE_VERSION or value.get("contract") != COVERAGE_CONTRACT_VERSION:
        return _empty_store()
    rows = value.get("items")
    if not isinstance(rows, dict):
        value["items"] = {}
    return value


def _save(value: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value["version"] = STORE_VERSION
    value["contract"] = COVERAGE_CONTRACT_VERSION
    value["updated_at"] = int(time.time())
    rows = value.setdefault("items", {})
    if len(rows) > 1000:
        ordered = sorted(
            rows.items(),
            key=lambda item: int((item[1] or {}).get("updated_at") or 0),
        )[-1000:]
        value["items"] = dict(ordered)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        "utf-8",
    )
    temporary.replace(path)


def coverage_key(publication_id: object, fingerprint: object) -> str:
    return "|".join(
        (
            COVERAGE_CONTRACT_VERSION,
            str(publication_id or ""),
            str(fingerprint or ""),
        )
    )


def get(publication_id: object, fingerprint: object) -> dict[str, Any] | None:
    row = _load().get("items", {}).get(coverage_key(publication_id, fingerprint))
    return dict(row) if isinstance(row, dict) else None


def ensure_pending(
    *,
    publication_id: object,
    fingerprint: object,
    retailer: object = None,
    title: object = None,
    valid_from: object = None,
) -> dict[str, Any]:
    store = _load()
    rows = store.setdefault("items", {})
    key = coverage_key(publication_id, fingerprint)
    existing = rows.get(key)
    if isinstance(existing, dict):
        return dict(existing)
    now = int(time.time())
    row = {
        "publication_id": str(publication_id or ""),
        "fingerprint": str(fingerprint or ""),
        "retailer": str(retailer or ""),
        "title": str(title or ""),
        "valid_from": str(valid_from or ""),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "pages_remaining": None,
        "pricing_remaining": None,
        "member_fallback_remaining": None,
        "hard_quarantined": 0,
    }
    rows[key] = row
    _save(store)
    return dict(row)


def update_snapshot(
    *,
    publication_id: object,
    fingerprint: object,
    retailer: object = None,
    title: object = None,
    valid_from: object = None,
    pages_remaining: int,
    pricing_remaining: int,
    member_fallback_remaining: int,
    hard_quarantined: int,
) -> dict[str, Any]:
    store = _load()
    rows = store.setdefault("items", {})
    key = coverage_key(publication_id, fingerprint)
    previous = rows.get(key) if isinstance(rows.get(key), dict) else {}
    now = int(time.time())
    remaining = max(0, int(pages_remaining)) + max(0, int(pricing_remaining)) + max(
        0, int(member_fallback_remaining)
    )
    if remaining:
        status = "pending"
    elif int(hard_quarantined) > 0:
        status = "degraded"
    else:
        status = "complete"

    row = {
        **previous,
        "publication_id": str(publication_id or ""),
        "fingerprint": str(fingerprint or ""),
        "retailer": str(retailer or previous.get("retailer") or ""),
        "title": str(title or previous.get("title") or ""),
        "valid_from": str(valid_from or previous.get("valid_from") or ""),
        "status": status,
        "pages_remaining": max(0, int(pages_remaining)),
        "pricing_remaining": max(0, int(pricing_remaining)),
        "member_fallback_remaining": max(0, int(member_fallback_remaining)),
        "hard_quarantined": max(0, int(hard_quarantined)),
        "updated_at": now,
    }
    row.setdefault("created_at", now)
    if status in _TERMINAL:
        row.setdefault("completed_at", now)
    else:
        row.pop("completed_at", None)
    rows[key] = row
    _save(store)
    return dict(row)


def notification_ready(record: dict[str, Any]) -> bool:
    publication_id = record.get("publication_id")
    fingerprint = record.get("fingerprint")
    if not publication_id or not fingerprint:
        return False
    row = get(publication_id, fingerprint)
    return bool(row and row.get("status") in _TERMINAL)


def status_payload() -> dict[str, Any]:
    store = _load()
    rows = [row for row in store.get("items", {}).values() if isinstance(row, dict)]
    counts = {"pending": 0, "complete": 0, "degraded": 0}
    for row in rows:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    pending = [
        {
            "publication_id": row.get("publication_id"),
            "retailer": row.get("retailer"),
            "title": row.get("title"),
            "pages_remaining": row.get("pages_remaining"),
            "pricing_remaining": row.get("pricing_remaining"),
            "member_fallback_remaining": row.get("member_fallback_remaining"),
        }
        for row in rows
        if row.get("status") == "pending"
    ]
    return {
        "contract": COVERAGE_CONTRACT_VERSION,
        "counts": counts,
        "pending": pending,
    }


__all__ = [
    "COVERAGE_CONTRACT_VERSION",
    "coverage_key",
    "ensure_pending",
    "get",
    "notification_ready",
    "status_payload",
    "update_snapshot",
]

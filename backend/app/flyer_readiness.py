from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .meny_flyer import Publication


STORE_VERSION = 1
_MEMBER_COVERAGE_SIGNAL = "member-price-context-nearby-v3"


def store_path() -> Path:
    explicit = os.getenv("FLYER_READINESS_STORE_PATH")
    if explicit:
        return Path(explicit)
    push_store = os.getenv("FLYER_PUSH_STORE_PATH")
    if push_store:
        return Path(push_store).with_name("flyer-readiness.json")
    return Path("/data/flyer-readiness.json")


def _lock_path() -> Path:
    path = store_path()
    return path.with_suffix(path.suffix + ".lock")


def _empty_store() -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "initialized": False,
        "publications": {},
        "updated_at": 0,
    }


def _read_unlocked() -> dict[str, Any]:
    path = store_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(value, dict):
        return _empty_store()
    value.setdefault("version", STORE_VERSION)
    value.setdefault("initialized", False)
    value.setdefault("publications", {})
    value.setdefault("updated_at", 0)
    return value


def _write_unlocked(store: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    store["version"] = STORE_VERSION
    store["updated_at"] = int(time.time())
    temporary.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        "utf-8",
    )
    temporary.replace(path)


@contextmanager
def _exclusive_store():
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        store = _read_unlocked()
        try:
            yield store
        finally:
            _write_unlocked(store)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_store() -> dict[str, Any]:
    path = store_path()
    if not path.exists():
        return _empty_store()

    lock_path = _lock_path()
    try:
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return _read_unlocked()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return _empty_store()


def readiness_revision() -> int:
    return int(load_store().get("updated_at") or 0)


def _stable_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _offer_signature(offer: Any) -> dict[str, Any]:
    payload = {
        "id": getattr(offer, "id", None),
        "page": getattr(offer, "page_number", None),
        "name": getattr(offer, "product_name", None),
        "price": getattr(offer, "price", None),
        "normal_price": getattr(offer, "normal_price", None),
        "raw_text": getattr(offer, "raw_text", None),
        "box": [
            getattr(offer, "hotspot_x", None),
            getattr(offer, "hotspot_y", None),
            getattr(offer, "hotspot_width", None),
            getattr(offer, "hotspot_height", None),
        ],
        "variants": [getattr(value, "name", None) for value in getattr(offer, "variants", [])],
    }
    coverage_signals = sorted({
        str(value)
        for value in (getattr(offer, "quality_signals", None) or [])
        if str(value) == _MEMBER_COVERAGE_SIGNAL
    })
    if coverage_signals:
        payload["coverage_signals"] = coverage_signals
    return payload


def page_fingerprints(publication: Publication) -> dict[str, str]:
    by_page: dict[int, list[Any]] = {}
    for offer in publication.structured_offers:
        page = offer.page_number
        if isinstance(page, int) and page > 0:
            by_page.setdefault(page, []).append(offer)

    result: dict[str, str] = {}
    for page_number in range(1, publication.page_count + 1):
        image = (
            publication.page_image_urls[page_number - 1]
            if page_number <= len(publication.page_image_urls)
            else ""
        )
        page_text = (
            publication.page_texts[page_number - 1]
            if page_number <= len(publication.page_texts)
            else ""
        )
        payload = {
            "page": page_number,
            "image": _stable_url(image),
            "text": page_text[:1600],
            "offers": [_offer_signature(value) for value in by_page.get(page_number, [])],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result[str(page_number)] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return result


def publication_fingerprint(publication: Publication) -> str:
    payload = {
        "id": publication.id,
        "retailer": publication.retailer,
        "title": publication.title,
        "valid_from": publication.valid_from,
        "valid_until": publication.valid_until,
        "page_count": publication.page_count,
        "pages": page_fingerprints(publication),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _snapshot(publication: Publication) -> dict[str, Any]:
    pages = page_fingerprints(publication)
    payload = {
        "publication_id": publication.id,
        "retailer": publication.retailer,
        "title": publication.title,
        "valid_from": publication.valid_from,
        "valid_until": publication.valid_until,
        "fingerprint": publication_fingerprint(publication),
        "page_fingerprints": pages,
    }
    return payload


def _changed_pages(previous: dict[str, Any] | None, current: dict[str, str]) -> list[int]:
    previous = previous if isinstance(previous, dict) else {}
    keys = set(previous) | set(current)
    return sorted(
        int(key)
        for key in keys
        if previous.get(key) != current.get(key) and str(key).isdigit()
    )


def observe_publications(
    publications: Iterable[Publication],
    *,
    bootstrap_ready_ids: set[str] | None = None,
) -> dict[str, Any]:
    current = list(publications)
    now = int(time.time())
    queued: list[str] = []
    changed: list[str] = []

    with _exclusive_store() as store:
        rows = store.setdefault("publications", {})
        first_observation = not bool(store.get("initialized"))

        for publication in current:
            snap = _snapshot(publication)
            existing = rows.get(publication.id)

            if first_observation and not isinstance(existing, dict):
                ready = bootstrap_ready_ids is None or publication.id in bootstrap_ready_ids
                rows[publication.id] = {
                    **snap,
                    "status": "ready" if ready else "processing",
                    "changed_pages": [] if ready else sorted(int(key) for key in snap["page_fingerprints"]),
                    "detected_at": now,
                    "ready_at": now if ready else None,
                    "processing_started_at": None if ready else now,
                    "attempts": 0,
                    "last_error": None,
                }
                if not ready:
                    queued.append(publication.id)
                continue

            if not isinstance(existing, dict):
                rows[publication.id] = {
                    **snap,
                    "status": "processing",
                    "changed_pages": sorted(int(key) for key in snap["page_fingerprints"]),
                    "detected_at": now,
                    "ready_at": None,
                    "processing_started_at": now,
                    "attempts": 0,
                    "last_error": None,
                }
                queued.append(publication.id)
                continue

            if existing.get("fingerprint") != snap["fingerprint"]:
                pages = _changed_pages(existing.get("page_fingerprints"), snap["page_fingerprints"])
                rows[publication.id] = {
                    **existing,
                    **snap,
                    "status": "processing",
                    "changed_pages": pages,
                    "detected_at": now,
                    "ready_at": None,
                    "processing_started_at": now,
                    "attempts": 0,
                    "last_error": None,
                }
                queued.append(publication.id)
                changed.append(publication.id)

        store["initialized"] = True

    return {
        "initialized": True,
        "queued": queued,
        "changed": changed,
        "current": len(current),
    }


def publication_is_ready(publication: Publication) -> bool:
    store = load_store()
    if not store.get("initialized"):
        return True
    row = store.get("publications", {}).get(publication.id)
    return bool(
        isinstance(row, dict)
        and row.get("status") == "ready"
        and row.get("fingerprint") == publication_fingerprint(publication)
    )


def filter_ready_publications(publications: Iterable[Publication]) -> list[Publication]:
    return [publication for publication in publications if publication_is_ready(publication)]


def pending_publication_records() -> list[dict[str, Any]]:
    store = load_store()
    result = [
        dict(row)
        for row in store.get("publications", {}).values()
        if isinstance(row, dict) and row.get("status") == "processing"
    ]
    return sorted(
        result,
        key=lambda row: (
            int(row.get("detected_at") or 0),
            str(row.get("publication_id") or ""),
        ),
    )


def ready_publication_records() -> list[dict[str, Any]]:
    store = load_store()
    result = [
        dict(row)
        for row in store.get("publications", {}).values()
        if isinstance(row, dict) and row.get("status") == "ready"
    ]
    return sorted(
        result,
        key=lambda row: (
            int(row.get("ready_at") or 0),
            str(row.get("publication_id") or ""),
        ),
    )


def mark_processing_attempt(publication_id: str, fingerprint: str) -> None:
    with _exclusive_store() as store:
        row = store.setdefault("publications", {}).get(publication_id)
        if not isinstance(row, dict) or row.get("fingerprint") != fingerprint:
            return
        row["status"] = "processing"
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["last_attempt_at"] = int(time.time())
        row["last_error"] = None


def mark_ready(publication: Publication) -> bool:
    snap = _snapshot(publication)
    now = int(time.time())
    with _exclusive_store() as store:
        row = store.setdefault("publications", {}).get(publication.id)
        if not isinstance(row, dict) or row.get("fingerprint") != snap["fingerprint"]:
            return False
        row.update({
            **snap,
            "status": "ready",
            "changed_pages": [],
            "ready_at": now,
            "last_error": None,
        })
        return True


def mark_failed(publication_id: str, fingerprint: str, error: str) -> None:
    with _exclusive_store() as store:
        row = store.setdefault("publications", {}).get(publication_id)
        if not isinstance(row, dict) or row.get("fingerprint") != fingerprint:
            return
        row["status"] = "processing"
        row["last_error"] = error[:500]
        row["last_failed_at"] = int(time.time())


def status_payload() -> dict[str, Any]:
    store = load_store()
    counts: dict[str, int] = {}
    for row in store.get("publications", {}).values():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "initialized": bool(store.get("initialized")),
        "counts": counts,
        "pending": [
            {
                "publication_id": row.get("publication_id"),
                "retailer": row.get("retailer"),
                "title": row.get("title"),
                "changed_pages": row.get("changed_pages", []),
                "attempts": row.get("attempts", 0),
                "last_error": row.get("last_error"),
            }
            for row in pending_publication_records()
        ],
    }

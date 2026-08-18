from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from .meny_flyer import Publication


_SERVING_CACHE_VERSION = 2


def serving_cache_path() -> Path:
    return Path(os.getenv("FLYER_SERVING_CACHE_PATH", "/data/flyer-serving-cache.json"))


def _not_expired(publication: Publication, *, today: date | None = None) -> bool:
    today = today or date.today()
    if publication.status == "expired":
        return False
    if not publication.valid_until:
        return True
    try:
        return datetime.strptime(publication.valid_until, "%d.%m.%Y").date() >= today
    except ValueError:
        return True


def load_verified_publications(*, today: date | None = None) -> list[Publication]:
    """Load the last verified flyer generation without any provider/network I/O.

    This is deliberately read-only. It exists so the mobile API can cold-start
    from the persistent serving snapshot after a NAS/container restart while a
    normal provider refresh happens asynchronously. Corrupt, old-schema,
    provisional and expired rows fail closed.
    """
    try:
        store = json.loads(serving_cache_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(store, dict) or store.get("version") != _SERVING_CACHE_VERSION:
        return []

    rows = store.get("publications")
    if not isinstance(rows, dict):
        return []

    result: list[Publication] = []
    for row in rows.values():
        if not isinstance(row, dict) or row.get("verified") is not True:
            continue
        payload = row.get("publication")
        if not isinstance(payload, dict):
            continue
        try:
            publication = Publication.model_validate(payload)
        except Exception:
            continue
        if _not_expired(publication, today=today):
            result.append(publication)

    return result


__all__ = ["load_verified_publications", "serving_cache_path"]

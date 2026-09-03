from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from .meny_flyer import Publication
from .retailer_sources import is_active_retailer


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


def _has_transient_meny_reader_urls(publication: Publication) -> bool:
    """MENY/iPaper CDN signatures are not durable serving-cache artifacts."""
    return bool(
        publication.retailer.casefold() == "meny"
        and publication.reader_kind == "embedded-viewer"
        and any("?" in value for value in publication.page_image_urls)
    )


def load_verified_publications(*, today: date | None = None) -> list[Publication]:
    """Load durable verified flyer generations without provider/network I/O.

    MENY is deliberately excluded when its iPaper pages use signed CDN query
    parameters. Those signatures can be invalidated at release rollover while
    the verified flyer metadata is still within its advertised validity window.
    Mobile API must therefore obtain MENY from a fresh provider pass instead of
    cold-starting from a visually broken persistent snapshot.

    Other retailers retain the existing stale-while-revalidate behavior.
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
        if not is_active_retailer(publication.retailer) or not _not_expired(publication, today=today):
            continue
        if _has_transient_meny_reader_urls(publication):
            continue
        result.append(publication)

    return result


__all__ = ["load_verified_publications", "serving_cache_path"]

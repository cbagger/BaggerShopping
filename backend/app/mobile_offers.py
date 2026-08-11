from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from .meny_flyer import Publication, fetch_meny_flyer, search_publication


router = APIRouter(prefix="/api/mobile/v1/offers", tags=["offers"])

_CACHE_TTL_SECONDS = 15 * 60
_publication_cache: Publication | None = None
_publication_cache_time = 0.0
_publication_lock = asyncio.Lock()


def _coverage_payload(publication: Publication) -> dict:
    offers_by_page: dict[int, int] = {}
    hotspots_by_page: dict[int, int] = {}
    for offer in publication.structured_offers:
        if offer.page_number is None:
            continue
        offers_by_page[offer.page_number] = offers_by_page.get(offer.page_number, 0) + 1
        if None not in (offer.hotspot_x, offer.hotspot_y, offer.hotspot_width, offer.hotspot_height):
            hotspots_by_page[offer.page_number] = hotspots_by_page.get(offer.page_number, 0) + 1
    pages = [
        {
            "page_number": page_number,
            "offer_count": offers_by_page.get(page_number, 0),
            "hotspot_count": hotspots_by_page.get(page_number, 0),
        }
        for page_number in range(1, publication.page_count + 1)
    ]
    return {
        "offer_count": sum(offers_by_page.values()),
        "hotspot_count": sum(hotspots_by_page.values()),
        "pages_without_hotspots": [
            page["page_number"]
            for page in pages
            if page["offer_count"] > 0 and page["hotspot_count"] == 0
        ],
        "pages": pages,
    }


def _publication_payload(publication: Publication) -> dict:
    return publication.model_dump(exclude={"text", "page_texts"})


async def _publication() -> Publication:
    global _publication_cache, _publication_cache_time
    now = time.monotonic()
    if (
        _publication_cache is not None
        and not _health_problems(_publication_cache)
        and now - _publication_cache_time < _CACHE_TTL_SECONDS
    ):
        return _publication_cache

    async with _publication_lock:
        now = time.monotonic()
        if (
            _publication_cache is not None
            and not _health_problems(_publication_cache)
            and now - _publication_cache_time < _CACHE_TTL_SECONDS
        ):
            return _publication_cache
        try:
            candidate = await fetch_meny_flyer()
            problems = _health_problems(candidate)
            if problems:
                raise ValueError("; ".join(problems))
            _publication_cache = candidate
            _publication_cache_time = now
            return candidate
        except Exception as exc:
            # A short upstream outage must not break a still-current flyer. An
            # expired cached flyer is never served, even when MENY is down.
            if _publication_cache is not None and not _health_problems(_publication_cache):
                return _publication_cache
            raise HTTPException(
                status_code=503,
                detail=f"Den aktuelle MENY-avis kunne ikke valideres: {exc}",
            ) from exc


def _parse_validity(value: str | None) -> date | None:
    try:
        return datetime.strptime(value or "", "%d.%m.%Y").date()
    except ValueError:
        return None


def _health_problems(publication: Publication, *, today: date | None = None) -> list[str]:
    """Report only failures that make the customer-facing flyer unusable."""
    today = today or date.today()
    problems: list[str] = []
    valid_until = _parse_validity(publication.valid_until)
    if valid_until is None:
        problems.append("gyldighedsdato mangler")
    elif valid_until < today:
        problems.append("avisen er udløbet")
    if publication.page_count <= 0 or len(publication.page_image_urls) != publication.page_count:
        problems.append("sidebilleder mangler")
    coverage = _coverage_payload(publication)
    offers = coverage["offer_count"]
    hotspots = coverage["hotspot_count"]
    if offers <= 0:
        problems.append("ingen tilbud fundet")
    elif hotspots / offers < 0.90:
        problems.append(f"kun {hotspots}/{offers} tilbud har markør")
    return problems


@router.get("/health")
async def offers_health():
    publication = await _publication()
    coverage = _coverage_payload(publication)
    return {
        "ok": True,
        "publication_id": publication.id,
        "title": publication.title,
        "valid_until": publication.valid_until,
        "status": publication.status,
        "coverage": coverage,
    }


@router.get("/publications")
async def publications():
    publication = await _publication()
    return {
        "ok": True,
        "publications": [_publication_payload(publication)],
        "offer_count": len(publication.structured_offers),
    }


@router.get("/search")
async def search_offers(
    q: str = Query(min_length=1, max_length=100),
    retailer: str = Query(default="MENY", max_length=40),
):
    if retailer.casefold() != "meny":
        raise HTTPException(status_code=400, detail="Retailer is not supported yet")
    publication = await _publication()
    result = search_publication(publication, q)
    return {
        "ok": True,
        "query": q,
        "retailer": "MENY",
        "publication": _publication_payload(publication),
        "offer_count": len(result.offers),
        "offers": [offer.model_dump() for offer in result.offers],
    }


@router.get("/publications/{publication_id}/offers")
async def publication_offers(publication_id: str):
    publication = await _publication()
    # The upstream iPaper URL can gain a different redirect/query string between
    # requests. There is only one current publication per retailer today, so the
    # id is a cache/version hint rather than an authorization boundary.
    return {
        "ok": True,
        "publication": _publication_payload(publication),
        "offers": [offer.model_dump() for offer in publication.structured_offers],
    }


@router.get("/current-offers")
async def current_publication_offers():
    """Return offers for the current flyer without a client-supplied id.

    There is currently one publication per retailer.  Keeping this as a static
    route also makes it straightforward for deployment smoke tests to prove
    that the complete offers router is running.
    """
    publication = await _publication()
    return {
        "ok": True,
        "publication": _publication_payload(publication),
        "offer_count": len(publication.structured_offers),
        "coverage": _coverage_payload(publication),
        "offers": [offer.model_dump() for offer in publication.structured_offers],
    }


# Compatibility for the already deployed proof-of-concept iOS build.
@router.get("/meny")
async def meny_offer_status():
    publication = await _publication()
    return {"ok": True, "retailer": "MENY", "publication": _publication_payload(publication)}


@router.get("/meny/search")
async def meny_offer_search(q: str = Query(min_length=1, max_length=100)):
    publication = await _publication()
    result = search_publication(publication, q)
    return {
        "ok": True,
        "retailer": "MENY",
        "query": q,
        "publication": _publication_payload(publication),
        "match_count": len(result.offers),
        "matches": [offer.raw_text for offer in result.offers],
    }

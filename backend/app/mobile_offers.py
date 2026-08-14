from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .flyer_adapters import RETAILER_ORDER, fetch_all_publications
from .meny_flyer import Offer, Publication, _is_pet_offer, _product_domain, search_publication
from .product_identity import MatchResult, compare


router = APIRouter(prefix="/api/mobile/v1/offers", tags=["offers"])

_CACHE_TTL_SECONDS = 15 * 60
_publication_cache: Publication | None = None
_publication_cache_time = 0.0
_publication_lock = asyncio.Lock()
_publications_cache: list[Publication] = []


class OfferMatchRequest(BaseModel):
    items: list[str] = Field(min_length=1, max_length=100)


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
    payload = publication.model_dump(exclude={"text", "page_texts"})
    payload["searchable"] = not _health_problems(publication)
    return payload


async def _publication() -> Publication:
    publications = await _publications()
    publication = next((item for item in publications if item.retailer.casefold() == "meny" and item.status == "current"), None)
    if publication is None:
        raise HTTPException(status_code=503, detail="Den aktuelle MENY-avis kunne ikke valideres")
    return publication


async def _publications() -> list[Publication]:
    global _publication_cache, _publication_cache_time, _publications_cache
    now = time.monotonic()
    if _publications_cache and now - _publication_cache_time < _CACHE_TTL_SECONDS:
        return _publications_cache

    async with _publication_lock:
        now = time.monotonic()
        if _publications_cache and now - _publication_cache_time < _CACHE_TTL_SECONDS:
            return _publications_cache
        try:
            candidates = await fetch_all_publications()
            # Only expose editions that support the complete product contract:
            # browse, search, positioned markers and add-to-list. Editorial or
            # supplementary catalogues without structured offers must not
            # masquerade as fully interactive flyers in the iPhone app.
            usable = [
                candidate for candidate in candidates
                if candidate.status != "expired"
                and not _reader_problems(candidate)
                and not _health_problems(candidate)
            ]
            if not usable:
                raise ValueError("ingen funktionelt gyldige aviser")
            _publications_cache = usable
            _publication_cache = next((item for item in usable if item.retailer == "MENY"), None)
            _publication_cache_time = now
            return usable
        except Exception as exc:
            fallback = [item for item in _publications_cache if item.status != "expired" and not _reader_problems(item)]
            if fallback:
                return fallback
            raise HTTPException(
                status_code=503,
                detail=f"De aktuelle tilbudsaviser kunne ikke valideres: {exc}",
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


def _reader_problems(publication: Publication, *, today: date | None = None) -> list[str]:
    """Report failures that make the page-based reader unusable."""
    today = today or date.today()
    problems: list[str] = []
    valid_until = _parse_validity(publication.valid_until)
    if valid_until is not None and valid_until < today:
        problems.append("avisen er udløbet")
    if publication.page_count <= 0 or len(publication.page_image_urls) != publication.page_count:
        problems.append("sidebilleder mangler")
    return problems


_MATCH_THRESHOLD = 62
_MAX_MATCHES_PER_ITEM = 4


def _identity_score(result: MatchResult) -> int:
    return {
        "same_item": 120,
        "compatible_variant": 78,
        "probably_same": 65,
        "not_same": 0,
    }[result.level]


def _text_match_score(item_name: str, candidate: str) -> int:
    return _identity_score(compare(item_name, candidate))


def _offer_match_result(item_name: str, offer: Offer) -> tuple[int, MatchResult]:
    query_domain = _product_domain(item_name)
    if _is_pet_offer(offer) and query_domain != "pet":
        result = compare(item_name, "kæledyrsfoder")
        return 0, result

    candidates = [offer.product_name, *(variant.name for variant in offer.variants)]
    if query_domain is not None:
        domains = {_product_domain(candidate) for candidate in candidates}
        concrete_domains = {domain for domain in domains if domain is not None}
        if concrete_domains and query_domain not in concrete_domains:
            result = compare(item_name, offer.product_name)
            return 0, result

    results = [(compare(item_name, candidate), candidate) for candidate in candidates]
    best, _ = max(results, key=lambda value: _identity_score(value[0]))
    return _identity_score(best), best


def _matched_offer(item_name: str, offer: Offer) -> tuple[int, Offer, MatchResult] | None:
    score, identity = _offer_match_result(item_name, offer)
    if score < _MATCH_THRESHOLD or offer.price is None:
        return None

    matched = offer.model_copy(deep=True)
    if matched.variants:
        relevant = [
            variant.model_copy(update={"matches_query": True})
            for variant in matched.variants
            if _text_match_score(item_name, variant.name) >= _MATCH_THRESHOLD
        ]
        # If the campaign heading itself is the match, retaining the campaign
        # variants is more informative than throwing them away. Otherwise only
        # expose the variants that actually matched the list item.
        if relevant:
            matched.variants = relevant
    return score, matched, identity


def match_items_to_publications(item_names: list[str], publications: list[Publication]) -> list[dict]:
    current = [publication for publication in publications if publication.status == "current"]
    groups: list[dict] = []
    seen_item_keys: set[str] = set()

    for raw_item_name in item_names:
        item_name = " ".join(raw_item_name.strip().split())
        item_key = item_name.casefold()
        if not item_name or item_key in seen_item_keys:
            continue
        seen_item_keys.add(item_key)

        candidates: dict[str, tuple[int, Offer, MatchResult]] = {}
        for publication in current:
            for offer in publication.structured_offers:
                result = _matched_offer(item_name, offer)
                if result is None:
                    continue
                score, matched, identity = result
                previous = candidates.get(matched.id)
                if previous is None or score > previous[0]:
                    candidates[matched.id] = (score, matched, identity)

        ranked = sorted(
            candidates.values(),
            key=lambda value: (-value[0], value[1].price if value[1].price is not None else float("inf"), value[1].retailer.casefold()),
        )[:_MAX_MATCHES_PER_ITEM]
        if ranked:
            groups.append({
                "item_name": item_name,
                "offers": [dict(offer.model_dump(), identity_match=identity.model_dump()) for _, offer, identity in ranked],
            })
    return groups


@router.get("/health")
async def offers_health():
    publications = await _publications()
    available = {publication.retailer for publication in publications}
    return {
        "ok": bool(publications),
        "degraded": any(retailer not in available for retailer in RETAILER_ORDER),
        "retailers": {
            retailer: [
                {
                    "publication_id": publication.id,
                    "title": publication.title,
                    "valid_until": publication.valid_until,
                    "status": publication.status,
                    "coverage": _coverage_payload(publication),
                }
                for publication in publications if publication.retailer == retailer
            ]
            for retailer in RETAILER_ORDER
        },
    }


@router.get("/publications")
async def publications():
    items = await _publications()
    return {
        "ok": True,
        "publications": [_publication_payload(publication) for publication in items],
        "offer_count": sum(len(publication.structured_offers) for publication in items),
        "retailers": [
            retailer for retailer in RETAILER_ORDER
            if any(publication.retailer == retailer for publication in items)
        ],
    }


@router.get("/search")
async def search_offers(
    q: str = Query(min_length=1, max_length=100),
    retailer: str | None = Query(default=None, max_length=40),
):
    selected = {value.strip().casefold() for value in (retailer or "").split(",") if value.strip()}
    items = [
        p for p in await _publications()
        if p.status == "current" and (not selected or p.retailer.casefold() in selected)
    ]
    if not items:
        raise HTTPException(status_code=404, detail="Der er ingen aktuel avis for den valgte butik")
    ranked: list[tuple[int, Offer, MatchResult]] = []
    for publication in items:
        # Retain the adapter's indexed lookup as a fast candidate source, but
        # always union it with all structured offers. Product identity is the
        # sole eligibility/ranking decision, so aliases can find offers even
        # when the retailer text search does not.
        indexed = search_publication(publication, q).offers
        candidates = {offer.id: offer for offer in [*publication.structured_offers, *indexed]}
        for offer in candidates.values():
            match = _matched_offer(q, offer)
            if match is not None:
                ranked.append(match)
    ranked.sort(key=lambda value: (-value[0], value[1].price if value[1].price is not None else float("inf"), value[1].retailer.casefold()))
    offers = [dict(offer.model_dump(), identity_match=identity.model_dump()) for _, offer, identity in ranked]
    return {
        "ok": True,
        "query": q,
        "retailer": items[0].retailer if len({item.retailer for item in items}) == 1 else "Alle butikker",
        "publication": _publication_payload(items[0]) if len(items) == 1 else None,
        "offer_count": len(offers),
        "offers": offers,
    }


@router.post("/matches")
async def smart_offer_matches(request: OfferMatchRequest):
    """Return conservative current-offer suggestions for ordinary list items.

    This route is read-only: it never changes Samsung items or shared offer
    metadata. A client must explicitly persist one of the returned offers after
    the user approves it.
    """
    groups = match_items_to_publications(request.items, await _publications())
    return {
        "ok": True,
        "item_count": len(groups),
        "offer_count": sum(len(group["offers"]) for group in groups),
        "matches": groups,
    }


@router.get("/publications/{publication_id}/offers")
async def publication_offers(publication_id: str):
    try:
        publication = next((item for item in await _publications() if item.id == publication_id), None)
    except HTTPException:
        publication = None
    if publication is None:
        # Compatibility with builds where MENY's upstream redirect changed the
        # derived id between the shelf request and opening the reader.
        publication = await _publication()
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

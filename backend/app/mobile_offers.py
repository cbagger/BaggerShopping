from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .flyer_publications import RETAILER_ORDER, fetch_all_publications
from .flyer_intelligence import (
    feedback_key,
    learned_adjustment,
    load_feedback_store,
    save_feedback_store,
    stable_report_id,
)
from .flyer_readiness import readiness_revision
from .flyer_serving_reader import load_verified_publications
from .meny_flyer import (
    Offer, Publication, _contains_query_term, _is_pet_offer, _product_domain,
    _query_terms, search_publication,
)
from .offer_serialization import customer_offer_payload
from .product_identity import MatchResult, analyze, apply_family_preference, compare


router = APIRouter(prefix="/api/mobile/v1/offers", tags=["offers"])

_CACHE_TTL_SECONDS = 15 * 60
_publication_cache: Publication | None = None
_publication_cache_time = 0.0
_publication_lock = asyncio.Lock()
_publications_cache: list[Publication] = []
_publication_refresh_task: asyncio.Task[None] | None = None
_publication_readiness_revision: str | None = None
_QUALITY_STORE_PATH = os.getenv("FLYER_QUALITY_STORE_PATH", "/data/flyer-quality-feedback.json")
_quality_store_lock = asyncio.Lock()


class OfferMatchRequest(BaseModel):
    items: list[str] = Field(min_length=1, max_length=100)


class FlyerQualityFeedbackRequest(BaseModel):
    publication_id: str = Field(min_length=1, max_length=100)
    offer_id: str | None = Field(default=None, max_length=100)
    retailer: str = Field(min_length=1, max_length=100)
    quality_source: str = Field(default="unknown", min_length=1, max_length=100)
    decision: str = Field(pattern="^(correct|wrong_position|wrong_variants|missing_offer|duplicate)$")
    page_number: int | None = Field(default=None, ge=1, le=500)
    note: str | None = Field(default=None, max_length=300)


def _coverage_payload(publication: Publication) -> dict:
    offers_by_page: dict[int, int] = {}
    hotspots_by_page: dict[int, int] = {}
    review_by_page: dict[int, int] = {}
    quality_scores: list[float] = []
    for offer in publication.structured_offers:
        if offer.page_number is None:
            continue
        offers_by_page[offer.page_number] = offers_by_page.get(offer.page_number, 0) + 1
        quality_scores.append(offer.quality_score)
        if None not in (offer.hotspot_x, offer.hotspot_y, offer.hotspot_width, offer.hotspot_height):
            hotspots_by_page[offer.page_number] = hotspots_by_page.get(offer.page_number, 0) + 1
        if offer.quality_score < 0.60 or offer.hotspot_confidence < 0.60:
            review_by_page[offer.page_number] = review_by_page.get(offer.page_number, 0) + 1
    pages = [
        {
            "page_number": page_number,
            "offer_count": offers_by_page.get(page_number, 0),
            "hotspot_count": hotspots_by_page.get(page_number, 0),
            "review_count": review_by_page.get(page_number, 0),
        }
        for page_number in range(1, publication.page_count + 1)
    ]
    return {
        "offer_count": sum(offers_by_page.values()),
        "hotspot_count": sum(hotspots_by_page.values()),
        "average_quality": round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else 0.0,
        "manual_review_count": sum(review_by_page.values()),
        "pages_requiring_review": sorted(review_by_page),
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


def _usable_publications(candidates: list[Publication]) -> list[Publication]:
    return [
        candidate for candidate in candidates
        if candidate.status != "expired"
        and not _reader_problems(candidate)
        and not _health_problems(candidate)
    ]


def _stale_publication_fallback() -> list[Publication]:
    return [
        item for item in _publications_cache
        if item.status != "expired" and not _reader_problems(item)
    ]


def _replace_publication_cache(publications: list[Publication], *, now: float | None = None) -> None:
    global _publication_cache, _publication_cache_time, _publications_cache
    _publications_cache = publications
    _publication_cache = next((item for item in publications if item.retailer == "MENY"), None)
    _publication_cache_time = time.monotonic() if now is None else now


async def _sync_readiness_revision() -> None:
    """Invalidate RAM cache when Luna publishes a new verified generation."""
    global _publication_cache, _publication_cache_time, _publications_cache
    global _publication_readiness_revision

    revision = readiness_revision()
    if _publication_readiness_revision is None:
        _publication_readiness_revision = revision
        return
    if revision == _publication_readiness_revision:
        return

    async with _publication_lock:
        revision = readiness_revision()
        if _publication_readiness_revision is None:
            _publication_readiness_revision = revision
            return
        if revision == _publication_readiness_revision:
            return
        _publications_cache = []
        _publication_cache = None
        _publication_cache_time = 0.0
        _publication_readiness_revision = revision


async def _refresh_publications_once() -> list[Publication] | None:
    """Refresh provider data without ever invalidating the last usable cache."""
    try:
        candidates = await fetch_all_publications()
        usable = _usable_publications(candidates)
    except Exception:
        return None

    if not usable:
        return None

    _replace_publication_cache(usable)
    return usable


async def _background_publication_refresh() -> None:
    async with _publication_lock:
        await _refresh_publications_once()


def _schedule_publication_refresh() -> None:
    global _publication_refresh_task
    if _publication_refresh_task is not None and not _publication_refresh_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _publication_refresh_task = loop.create_task(_background_publication_refresh())


async def _publications() -> list[Publication]:
    await _sync_readiness_revision()

    now = time.monotonic()
    if _publications_cache:
        if now - _publication_cache_time < _CACHE_TTL_SECONDS:
            return _publications_cache
        fallback = _stale_publication_fallback()
        if fallback:
            _schedule_publication_refresh()
            return fallback

    async with _publication_lock:
        now = time.monotonic()
        if _publications_cache:
            if now - _publication_cache_time < _CACHE_TTL_SECONDS:
                return _publications_cache
            fallback = _stale_publication_fallback()
            if fallback:
                _schedule_publication_refresh()
                return fallback

        cached = _usable_publications(load_verified_publications())
        if cached:
            _replace_publication_cache(cached, now=now)
            _schedule_publication_refresh()
            return cached

        refreshed = await _refresh_publications_once()
        if refreshed:
            return refreshed

        fallback = _stale_publication_fallback()
        if fallback:
            return fallback

        raise HTTPException(
            status_code=503,
            detail="De aktuelle tilbudsaviser kunne ikke valideres",
        )


def _parse_validity(value: str | None) -> date | None:
    try:
        return datetime.strptime(value or "", "%d.%m.%Y").date()
    except ValueError:
        return None


def _health_problems(publication: Publication, *, today: date | None = None) -> list[str]:
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
    best, candidate = max(results, key=lambda value: _identity_score(value[0]))
    return apply_family_preference(item_name, candidate, _identity_score(best), best)


def _offer_payload(
    offer: Offer,
    identity: MatchResult | None = None,
    publication_status: str | None = None,
    *,
    include_identity: bool = True,
) -> dict:
    payload = customer_offer_payload(offer)
    learning = learned_adjustment(
        load_feedback_store(_QUALITY_STORE_PATH), offer.retailer,
        offer.quality_source, offer.publication_id,
    )
    payload["quality_score"] = round(max(0.0, min(1.0, offer.quality_score + learning.score)), 3)
    payload["hotspot_confidence"] = round(max(
        0.0, offer.hotspot_confidence - min(0.18, learning.position_reports * 0.02)
    ), 3)
    payload["variant_confidence"] = round(max(
        0.0, offer.variant_confidence - min(0.18, learning.variant_reports * 0.02)
    ), 3)
    payload["learning_reports"] = {
        "wrong_position": learning.position_reports,
        "wrong_variants": learning.variant_reports,
    }

    payload["variants"] = []
    if include_identity:
        payload["product_identity"] = analyze(
            offer.product_name, quantity=offer.quantity, unit=offer.unit, price=offer.price,
        ).model_dump()
        for variant in offer.variants:
            value = variant.model_dump()
            value["identity"] = analyze(
                variant.name,
                quantity=variant.quantity if variant.quantity is not None else offer.quantity,
                unit=variant.unit or offer.unit,
                price=offer.price,
            ).model_dump()
            payload["variants"].append(value)
    else:
        payload["variants"] = [variant.model_dump() for variant in offer.variants]

    if identity is not None:
        payload["identity_match"] = identity.model_dump()
    if publication_status is not None:
        payload["publication_status"] = publication_status
    return payload


@router.post("/quality/feedback")
async def flyer_quality_feedback(request: FlyerQualityFeedbackRequest):
    publications = await _publications()
    publication = next(
        (value for value in publications if value.id == request.publication_id), None,
    )
    if publication is None:
        raise HTTPException(status_code=404, detail="Tilbudsavisen findes ikke længere")
    authoritative_offer = next(
        (value for value in publication.structured_offers if value.id == request.offer_id), None,
    ) if request.offer_id else None
    if request.decision != "missing_offer" and authoritative_offer is None:
        raise HTTPException(status_code=404, detail="Tilbuddet findes ikke i den valgte avis")
    retailer = authoritative_offer.retailer if authoritative_offer else publication.retailer
    quality_source = authoritative_offer.quality_source if authoritative_offer else publication.content_source or "unknown"
    page_number = authoritative_offer.page_number if authoritative_offer else request.page_number
    created_at = int(time.time())
    async with _quality_store_lock:
        store = load_feedback_store(_QUALITY_STORE_PATH)
        source_key = feedback_key(retailer, quality_source)
        publication_key = feedback_key(retailer, quality_source, request.publication_id)
        for bucket, key in (("sources", source_key), ("publications", publication_key)):
            row = store.setdefault(bucket, {}).setdefault(key, {})
            row[request.decision] = int(row.get(request.decision, 0)) + 1
            row["updated_at"] = created_at
        reports = store.setdefault("reports", [])
        reports.append({
            "id": stable_report_id(request.publication_id, request.offer_id or "missing", request.decision, created_at),
            "publication_id": request.publication_id,
            "offer_id": request.offer_id,
            "retailer": retailer,
            "quality_source": quality_source,
            "decision": request.decision,
            "page_number": page_number,
            "note": request.note,
            "created_at": created_at,
        })
        store["reports"] = reports[-1000:]
        save_feedback_store(_QUALITY_STORE_PATH, store)
    return {"ok": True, "decision": request.decision, "source_key": source_key, "publication_key": publication_key}


@router.get("/quality/status")
async def flyer_quality_status():
    store = load_feedback_store(_QUALITY_STORE_PATH)
    return {
        "ok": True,
        "source_count": len(store.get("sources", {})),
        "publication_count": len(store.get("publications", {})),
        "report_count": len(store.get("reports", [])),
        "sources": store.get("sources", {}),
        "publications": store.get("publications", {}),
    }


def _search_match_result(item_name: str, offer: Offer) -> tuple[int, Offer, MatchResult] | None:
    query_domain = _product_domain(item_name)
    if _is_pet_offer(offer) and query_domain != "pet":
        return None

    terms = _query_terms(item_name)
    product_match = _contains_query_term(offer.product_name, terms)
    brand_match = bool(offer.brand and _contains_query_term(offer.brand, terms))
    raw_match = bool(offer.raw_text and _contains_query_term(offer.raw_text, terms))
    matching_ids = {
        variant.id for variant in offer.variants
        if _contains_query_term(variant.name, terms)
        and (query_domain is None or _product_domain(variant.name) in {None, query_domain})
    }

    raw_identity = compare(item_name, offer.raw_text) if raw_match else None
    offer_domains = {
        value for candidate in [offer.product_name, *(variant.name for variant in offer.variants)]
        if (value := _product_domain(candidate)) is not None
    }
    trusted_product_match = bool(product_match and (query_domain is None or not offer_domains or query_domain in offer_domains))
    trusted_raw_match = bool(
        raw_match and (
            (raw_identity is not None and raw_identity.level != "not_same")
            or (query_domain is not None and query_domain in offer_domains)
        )
    )

    if trusted_product_match or brand_match or trusted_raw_match or matching_ids:
        matched = offer.model_copy(deep=True)
        matched.variants = [
            variant.model_copy(update={"matches_query": variant.id in matching_ids})
            for variant in matched.variants
        ]
        candidates = [offer.product_name, *(variant.name for variant in offer.variants)]
        identity = max((compare(item_name, candidate) for candidate in candidates), key=_identity_score)
        if identity.level == "not_same":
            identity = identity.model_copy(update={
                "level": "probably_same",
                "confidence": .82,
                "explanation": "Direkte tekstmatch i tilbuddet eller en af dets varianter.",
            })
        score = 150 if matching_ids else 145 if trusted_product_match or brand_match else 125
        score, identity = apply_family_preference(item_name, offer.product_name, score, identity)
        if score <= 0:
            return None
        return score, matched, identity

    return _matched_offer(item_name, offer, preserve_variants=True)


def _search_deduplication_key(offer: Offer) -> tuple:
    return (
        offer.retailer.casefold(), offer.publication_id, offer.page_number,
        " ".join(offer.product_name.casefold().split()), offer.price,
        offer.quantity, (offer.unit or "").casefold(),
    )


def _matched_offer(item_name: str, offer: Offer, *, preserve_variants: bool = False) -> tuple[int, Offer, MatchResult] | None:
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
        if preserve_variants:
            relevant_ids = {variant.id for variant in relevant}
            matched.variants = [
                variant.model_copy(update={"matches_query": variant.id in relevant_ids})
                for variant in matched.variants
            ]
        elif relevant:
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
                "offers": [_offer_payload(offer, identity) for _, offer, identity in ranked],
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
    term = " ".join(q.split())
    if not term:
        raise HTTPException(status_code=400, detail="Skriv et søgeord")
    selected = {value.strip().casefold() for value in (retailer or "").split(",") if value.strip()}
    items = [
        p for p in await _publications()
        if p.status in {"current", "upcoming"}
        and (not selected or p.retailer.casefold() in selected)
    ]
    if not items:
        raise HTTPException(status_code=404, detail="Der er ingen aktuel eller kommende avis for den valgte butik")
    ranked: dict[tuple, tuple[int, Offer, MatchResult, str]] = {}
    for publication in items:
        for offer in publication.structured_offers:
            match = _search_match_result(term, offer)
            if match is not None:
                score, matched, identity = match
                key = _search_deduplication_key(matched)
                previous = ranked.get(key)
                if previous is None or score > previous[0] or len(matched.variants) > len(previous[1].variants):
                    ranked[key] = (score, matched, identity, publication.status)
    ordered = sorted(
        ranked.values(),
        key=lambda value: (
            value[3] != "current", -value[0],
            value[1].price if value[1].price is not None else float("inf"),
            value[1].retailer.casefold(),
        ),
    )
    offers = [_offer_payload(offer, identity, publication_status) for _, offer, identity, publication_status in ordered]
    return {
        "ok": True,
        "query": term,
        "retailer": items[0].retailer if len({item.retailer for item in items}) == 1 else "Alle butikker",
        "publication": _publication_payload(items[0]) if len(items) == 1 else None,
        "offer_count": len(offers),
        "offers": offers,
    }


@router.post("/matches")
async def smart_offer_matches(request: OfferMatchRequest):
    groups = match_items_to_publications(request.items, await _publications())
    return {
        "ok": True,
        "item_count": len(groups),
        "offer_count": sum(len(group["offers"]) for group in groups),
        "matches": groups,
    }


@router.get("/publications/{publication_id}/offers")
async def publication_offers(publication_id: str):
    publication = next((item for item in await _publications() if item.id == publication_id), None)
    if publication is None:
        raise HTTPException(status_code=404, detail="Tilbudsavisen findes ikke længere")
    return {
        "ok": True,
        "publication": _publication_payload(publication),
        "offers": [_offer_payload(offer, include_identity=False) for offer in publication.structured_offers],
    }


@router.get("/current-offers")
async def current_publication_offers():
    publication = await _publication()
    return {
        "ok": True,
        "publication": _publication_payload(publication),
        "offer_count": len(publication.structured_offers),
        "coverage": _coverage_payload(publication),
        "offers": [_offer_payload(offer, include_identity=False) for offer in publication.structured_offers],
    }


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

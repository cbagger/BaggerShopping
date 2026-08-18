"""Kurv backend compatibility and performance hooks.

Provider polygons are allowed to be slightly imperfect so visible offers are not
silently lost. Duplicate coupling is deliberately conservative: only almost
identical source regions are merged, while distinct visual offers keep separate
'+' markers.

Customer-facing offer serialization is intentionally *not* installed here.
Derived member-pricing metadata now lives behind the explicit
``offer_serialization.customer_offer_payload`` API boundary.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import flyer_intelligence as _fi
from . import meny_flyer as _mf
from .member_pricing_sources import (
    enrich_ipaper_offers,
    enrich_schwarz_publication,
    enrich_tjek_offers,
)
from .variant_extractor_v2 import extract_variants_v2 as _extract_variants_v2


def _recall_first_box_from_polygon(
    points: Iterable[Sequence[object]],
    *,
    vertical_scale: float = 1.0,
    source: str = "native-polygon",
):
    if vertical_scale <= 0 or vertical_scale != vertical_scale:
        return None

    parsed: list[tuple[float, float]] = []
    for point in points:
        if len(point) < 2:
            continue
        x = _fi._number(point[0])
        y = _fi._number(point[1])
        if x is not None and y is not None:
            parsed.append((x, y / vertical_scale))

    if len(parsed) < 2:
        return None

    xs, ys = zip(*parsed)
    x = max(0.0, min(1.0, min(xs)))
    y = max(0.0, min(1.0, min(ys)))
    width = max(0.0001, min(1.0 - x, max(xs) - min(xs)))
    height = max(0.0001, min(1.0 - y, max(ys) - min(ys)))
    if width <= 0 or height <= 0:
        return None

    area = width * height
    confidence = 0.97 if source in {"native", "tjek-polygon", "ipaper-marker"} else 0.82
    if area > 0.65:
        confidence -= 0.25
    elif area < 0.0015:
        confidence -= 0.12

    return _fi.HotspotBox(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=max(0.35, confidence),
        source=source,
    )


def _recall_first_couple_offers(offers):
    """Merge only near-identical duplicates, never merely nearby offers."""
    result = []
    for offer in offers:
        box = _fi._offer_box(offer)
        match_index = None
        for index, existing in enumerate(result):
            if existing.page_number != offer.page_number or existing.price != offer.price:
                continue
            existing_box = _fi._offer_box(existing)
            same_label = _fi._space(existing.product_name).casefold() == _fi._space(offer.product_name).casefold()
            if same_label and (
                existing_box is None
                or box is None
                or _fi.intersection_over_union(existing_box, box) >= 0.90
            ):
                match_index = index
                break

        if match_index is None:
            result.append(offer)
            continue

        existing = result[match_index]
        variants = list(existing.variants)
        seen = {variant.name.casefold() for variant in variants}
        for variant in offer.variants:
            key = variant.name.casefold()
            if key not in seen:
                variants.append(variant)
                seen.add(key)

        existing_box = _fi._offer_box(existing)
        union = _fi.union_boxes(value for value in (existing_box, box) if value is not None)
        updates = {
            "variants": variants,
            "raw_text": " | ".join(dict.fromkeys(filter(None, (existing.raw_text, offer.raw_text)))),
            "quality_score": max(existing.quality_score, offer.quality_score),
            "variant_confidence": max(existing.variant_confidence, offer.variant_confidence),
            "quality_issues": list(dict.fromkeys([*existing.quality_issues, *offer.quality_issues])),
            "quality_signals": list(dict.fromkeys([
                *existing.quality_signals, *offer.quality_signals, "coupled-source-rows",
            ])),
        }
        if union is not None:
            updates.update({
                "hotspot_x": union.x,
                "hotspot_y": union.y,
                "hotspot_width": union.width,
                "hotspot_height": union.height,
                "hotspot_confidence": union.confidence,
            })
        result[match_index] = existing.model_copy(update=updates)

    return sorted(
        result,
        key=lambda value: (
            value.page_number or 0,
            value.hotspot_y or 0,
            value.hotspot_x or 0,
        ),
    )


_fi.box_from_polygon = _recall_first_box_from_polygon
_fi.couple_offers = _recall_first_couple_offers
# Variant Extractor v2 is deliberately text/structure-only. It never reads
# image labels or uses package weight/unit price to invent or rank variants.
# Patching the shared function here makes Tjek/Schwarz adapters use v2 without
# changing their provider-specific geometry and source handling.
_fi.extract_variants = _extract_variants_v2


# Membership price recognition needs the text surrounding the actual advert,
# not a whole-page image recognizer. The provider adapters already carry page
# text/OCR/structured metadata but historically discarded some of it before an
# Offer reached the public API. Enrich only raw_text; hotspot geometry, variant
# extraction and source prices remain untouched.
_original_parse_enrichment_chunks = _mf.parse_enrichment_chunks


def _member_context_parse_enrichment_chunks(publication, chunks):
    offers = _original_parse_enrichment_chunks(publication, chunks)
    return enrich_ipaper_offers(publication, offers)


_mf.parse_enrichment_chunks = _member_context_parse_enrichment_chunks

# Import after the shared iPaper/variant hooks above so flyer_adapters binds the
# patched functions at module import time.
from . import flyer_adapters as _fa  # noqa: E402

_original_parse_tjek_hotspots = _fa.parse_tjek_hotspots
_original_publication_from_schwarz = _fa._publication_from_schwarz


def _member_context_parse_tjek_hotspots(publication, rows, offer_rows=None):
    offers = _original_parse_tjek_hotspots(publication, rows, offer_rows)
    return enrich_tjek_offers(offers, rows, offer_rows)


def _member_context_publication_from_schwarz(payload, source, reader_url):
    publication = _original_publication_from_schwarz(payload, source, reader_url)
    return enrich_schwarz_publication(publication, payload)


_fa.parse_tjek_hotspots = _member_context_parse_tjek_hotspots
_fa._publication_from_schwarz = _member_context_publication_from_schwarz


# Luna is an optional cached overlay, never a flyer-discovery dependency. Keep
# the raw deterministic fetcher exported for the Luna worker, then let ordinary
# Kurv consumers see only already-verified cached brand/variant enrichment. The
# overlay itself makes no network/OpenAI calls, and returns the original objects
# unchanged while Luna is OFF.
from .luna_overlay import apply_cached_enrichment as _apply_cached_luna_enrichment  # noqa: E402

_original_fetch_all_publications = _fa.fetch_all_publications


async def _luna_aware_fetch_all_publications(*args, **kwargs):
    publications = await _original_fetch_all_publications(*args, **kwargs)
    try:
        return _apply_cached_luna_enrichment(publications)
    except Exception:
        # AI telemetry/cache corruption must never reduce normal Kurv function.
        return publications


_fa.fetch_all_publications = _luna_aware_fetch_all_publications

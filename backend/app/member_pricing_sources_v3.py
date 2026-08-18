from __future__ import annotations

from . import member_pricing_sources_v2 as v2
from .member_pricing import has_membership_signal
from .meny_flyer import Offer, Publication


COVERAGE_SIGNAL = "member-price-context-nearby-v3"


def _add_signal(offer: Offer) -> Offer:
    if COVERAGE_SIGNAL in offer.quality_signals:
        return offer
    return offer.model_copy(
        update={"quality_signals": [*offer.quality_signals, COVERAGE_SIGNAL]}
    )


def enrich_ipaper_offers(publication: Publication, offers: list[Offer]) -> list[Offer]:
    """Keep page membership context broad enough to trigger exact crop review.

    The page snippet is still wrapped as page-only context, so deterministic
    customer pricing never treats a neighbouring badge as this offer's member
    price. The only new behaviour is recall: a nearby membership signal marks
    the hotspot for semantic/crop verification.
    """
    result: list[Offer] = []
    for offer in offers:
        context = ""
        if offer.page_number is not None and 0 < offer.page_number <= len(publication.page_texts):
            candidate = v2._localized_context(
                publication.page_texts[offer.page_number - 1],
                v2._significant_needles(offer),
                radius=300,
            )
            if has_membership_signal(candidate):
                context = candidate
        updated = v2._append_context(offer, [context], page_context=True)
        if context:
            updated = _add_signal(updated)
        result.append(updated)
    return result


def enrich_tjek_offers(
    offers: list[Offer],
    hotspot_rows: object,
    detailed_rows: object = None,
) -> list[Offer]:
    enriched = v2.enrich_tjek_offers(offers, hotspot_rows, detailed_rows)
    return [
        _add_signal(offer) if has_membership_signal(offer.raw_text) else offer
        for offer in enriched
    ]


def enrich_schwarz_publication(publication: Publication, payload: object) -> Publication:
    enriched = v2.enrich_schwarz_publication(publication, payload)
    enriched.structured_offers = [
        _add_signal(offer) if has_membership_signal(offer.raw_text) else offer
        for offer in enriched.structured_offers
    ]
    return enriched


__all__ = [
    "COVERAGE_SIGNAL",
    "enrich_ipaper_offers",
    "enrich_schwarz_publication",
    "enrich_tjek_offers",
]

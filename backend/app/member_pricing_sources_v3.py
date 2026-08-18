from __future__ import annotations

from . import member_pricing_sources_v2 as v2
from .member_pricing import has_membership_signal
from .meny_flyer import Offer, Publication


COVERAGE_SIGNAL = "member-price-context-nearby-v3"
PAGE_COHORT_SIGNAL = COVERAGE_SIGNAL


def _add_signal(offer: Offer, signal: str = COVERAGE_SIGNAL) -> Offer:
    if signal in offer.quality_signals:
        return offer
    return offer.model_copy(
        update={"quality_signals": [*offer.quality_signals, signal]}
    )


def _page_key(offer: Offer) -> tuple[str, int] | None:
    if offer.page_number is None:
        return None
    return offer.publication_id, offer.page_number


def _mark_member_signal_pages(offers: list[Offer]) -> list[Offer]:
    """Escalate every hotspot on a page that contains a member-price signal.

    This is recall evidence only. It never copies a price or membership label
    between offers. The semantic guard uses the signal solely to require an
    exact target crop when the page audit did not confirm a member-price badge
    for that hotspot.
    """
    pages = {
        key
        for offer in offers
        if (key := _page_key(offer)) is not None
        and has_membership_signal(offer.raw_text)
    }
    if not pages:
        return offers
    return [
        _add_signal(offer, PAGE_COHORT_SIGNAL)
        if _page_key(offer) in pages else offer
        for offer in offers
    ]


def enrich_ipaper_offers(publication: Publication, offers: list[Offer]) -> list[Offer]:
    result: list[Offer] = []
    member_pages = {
        index
        for index, text in enumerate(publication.page_texts, start=1)
        if has_membership_signal(text)
    }
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
        if offer.page_number in member_pages:
            updated = _add_signal(updated, PAGE_COHORT_SIGNAL)
        result.append(updated)
    return result


def enrich_tjek_offers(
    offers: list[Offer],
    hotspot_rows: object,
    detailed_rows: object = None,
) -> list[Offer]:
    enriched = v2.enrich_tjek_offers(offers, hotspot_rows, detailed_rows)
    individually_marked = [
        _add_signal(offer) if has_membership_signal(offer.raw_text) else offer
        for offer in enriched
    ]
    return _mark_member_signal_pages(individually_marked)


def enrich_schwarz_publication(publication: Publication, payload: object) -> Publication:
    enriched = v2.enrich_schwarz_publication(publication, payload)
    individually_marked = [
        _add_signal(offer) if has_membership_signal(offer.raw_text) else offer
        for offer in enriched.structured_offers
    ]
    enriched.structured_offers = _mark_member_signal_pages(individually_marked)
    return enriched


__all__ = [
    "COVERAGE_SIGNAL",
    "PAGE_COHORT_SIGNAL",
    "enrich_ipaper_offers",
    "enrich_schwarz_publication",
    "enrich_tjek_offers",
]

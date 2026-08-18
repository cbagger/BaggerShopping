from __future__ import annotations

from . import member_pricing_sources_v2 as v2
from . import member_pricing_v3 as pricing_v3
from .member_pricing import detect_member_pricing, has_membership_signal
from .meny_flyer import Offer, Publication, _normalize_space


COVERAGE_SIGNAL = "member-price-context-nearby-v3"
# Kept as a compatibility alias for older diagnostics/tests. The signal is no
# longer propagated to every hotspot on a page; it now always means that this
# exact offer has provider/localized membership evidence.
PAGE_COHORT_SIGNAL = COVERAGE_SIGNAL


def _add_signal(offer: Offer, signal: str = COVERAGE_SIGNAL) -> Offer:
    if signal in offer.quality_signals:
        return offer
    return offer.model_copy(
        update={"quality_signals": [*offer.quality_signals, signal]}
    )


def _mark_member_signal_pages(offers: list[Offer]) -> list[Offer]:
    """Mark only offers whose own enriched context contains membership evidence.

    The previous v3 implementation escalated every hotspot on a page whenever
    any one offer mentioned a member programme. That gave excellent recall but
    turned pages with Lidl Plus / Netto+ into dozens of mandatory paid crops.

    v2 enrichment already binds structured provider data and localized page
    context to an individual offer. At this layer we therefore mark only the
    exact enriched offer and never copy the signal to siblings on the page.
    The old function name remains for compatibility with callers.
    """
    return [
        _add_signal(offer) if has_membership_signal(offer.raw_text) else offer
        for offer in offers
    ]


def _target_anchor(text: str, offer: Offer) -> tuple[int, int] | None:
    """Find one strong iPaper text anchor for the exact offer.

    Prefer the campaign heading because it is the same label used by the
    positioned iPaper marker. Fall back to the longest structured variant only
    when the heading is absent from pageTexts.
    """
    folded = text.casefold()
    names = [offer.product_name, *(variant.name for variant in offer.variants)]
    for raw in sorted(names, key=lambda value: (value != offer.product_name, -len(value))):
        needle = _normalize_space(raw)
        if len(needle) < 4:
            continue
        index = folded.find(needle.casefold())
        if index >= 0:
            return index, len(needle)
    return None


def _exact_meny_member_context(page_text: str, offer: Offer) -> str:
    """Return customer-safe MENY member-price text for one exact iPaper offer.

    iPaper exposes pageTexts in visual reading order. Historically Kurv treated
    every pageText fragment as review-only context, which meant even explicit
    MENY ``MEDLEMSPRIS`` price pairs disappeared unless Luna had already audited
    the hotspot. We can resolve the common MENY shape deterministically without
    weakening neighbour safety:

    * the exact campaign/variant must anchor the local window;
    * the provider's structured selling price must occur after that anchor and
      before the explicit member-price marker;
    * no other non-unit selling price may sit between the anchor and the
      provider price, or between the provider price and the member marker; and
    * the ordinary/member roles must pass the normal generic pricing classifier.

    Unit/reference prices remain allowed because they are explicitly classified
    as such. Competing selling prices make the local reading order ambiguous and
    therefore fail closed instead of borrowing a neighbour's member price.
    """
    if offer.retailer.casefold().strip() != "meny" or offer.price is None:
        return ""

    text = _normalize_space(page_text)
    if not text:
        return ""
    anchor = _target_anchor(text, offer)
    if anchor is None:
        return ""

    anchor_start, anchor_length = anchor
    left = max(0, anchor_start - 30)
    right = min(len(text), anchor_start + anchor_length + 460)
    window = text[left:right]
    anchor_in_window = anchor_start - left

    prices = pricing_v3._price_candidates(window)
    markers = [
        marker
        for marker in pricing_v3.EXPLICIT_MEMBER_MARKER_RE.finditer(window)
        if marker.start() >= anchor_in_window
    ]
    if not markers:
        return ""

    for marker in markers:
        ordinary_candidates = [
            candidate
            for candidate in prices
            if candidate.start >= anchor_in_window
            and candidate.end <= marker.start()
            and pricing_v3._same_price(candidate.value, offer.price)
            and not candidate.unit_price_context
            and not candidate.membership_fee_context
            and not candidate.before_role
        ]
        if not ordinary_candidates:
            continue

        ordinary = ordinary_candidates[-1]
        if marker.start() - ordinary.end > 100:
            continue

        competing_before_ordinary = [
            candidate
            for candidate in prices
            if candidate.start >= anchor_in_window
            and candidate.end <= ordinary.start
            and not candidate.unit_price_context
            and not candidate.membership_fee_context
            and not candidate.before_role
            and not pricing_v3._same_price(candidate.value, ordinary.value)
        ]
        if competing_before_ordinary:
            continue

        competing_after_ordinary = [
            candidate
            for candidate in prices
            if candidate.start >= ordinary.end
            and candidate.end <= marker.start()
            and not candidate.unit_price_context
            and not candidate.membership_fee_context
            and not candidate.before_role
            and not pricing_v3._same_price(candidate.value, ordinary.value)
        ]
        if competing_after_ordinary:
            continue

        context_start = max(anchor_in_window, ordinary.start - 120)
        context_end = min(len(window), marker.end() + 150)
        context = _normalize_space(window[context_start:context_end])
        pricing = detect_member_pricing(
            retailer=offer.retailer,
            price=offer.price,
            normal_price=offer.normal_price,
            text=context,
            unit_price=offer.unit_price,
        )
        if pricing is None:
            continue
        if pricing.ordinary_price is None or not pricing_v3._same_price(
            pricing.ordinary_price, offer.price
        ):
            continue
        if pricing.member_price >= offer.price - 0.005:
            continue
        return context

    return ""


def enrich_ipaper_offers(publication: Publication, offers: list[Offer]) -> list[Offer]:
    result: list[Offer] = []
    for offer in offers:
        context = ""
        exact_context = ""
        if offer.page_number is not None and 0 < offer.page_number <= len(publication.page_texts):
            page_text = publication.page_texts[offer.page_number - 1]
            candidate = v2._localized_context(
                page_text,
                v2._significant_needles(offer),
                radius=300,
            )
            if has_membership_signal(candidate):
                context = candidate
            exact_context = _exact_meny_member_context(page_text, offer)

        # Exact MENY price-role context is customer-safe and may be interpreted
        # deterministically. Broader localized context remains review evidence
        # only. Crucially, membership evidence is never propagated to unrelated
        # hotspots elsewhere on the same page.
        updated = v2._append_context(offer, [exact_context])
        updated = v2._append_context(updated, [context], page_context=True)
        if context or exact_context:
            updated = _add_signal(updated)
        result.append(updated)
    return result


def enrich_tjek_offers(
    offers: list[Offer],
    hotspot_rows: object,
    detailed_rows: object = None,
) -> list[Offer]:
    enriched = v2.enrich_tjek_offers(offers, hotspot_rows, detailed_rows)
    return _mark_member_signal_pages(enriched)


def enrich_schwarz_publication(publication: Publication, payload: object) -> Publication:
    enriched = v2.enrich_schwarz_publication(publication, payload)
    enriched.structured_offers = _mark_member_signal_pages(enriched.structured_offers)
    return enriched


__all__ = [
    "COVERAGE_SIGNAL",
    "PAGE_COHORT_SIGNAL",
    "enrich_ipaper_offers",
    "enrich_schwarz_publication",
    "enrich_tjek_offers",
]

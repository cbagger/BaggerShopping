from app.luna_semantic_guards import _pricing_sanity_reasons
from app.member_pricing import detect_member_pricing
from app.member_pricing_sources_v3 import COVERAGE_SIGNAL, enrich_ipaper_offers
from app.meny_flyer import Offer, Publication


def offer(*, name="Riberhus skiveost", price=24.0) -> Offer:
    return Offer(
        id="cheese-1",
        retailer="Netto",
        publication_id="netto-current",
        publication_title="Netto uge 34",
        product_name=name,
        price=price,
        source_url="https://netto.test",
        page_number=10,
        hotspot_x=0.55,
        hotspot_y=0.35,
        hotspot_width=0.20,
        hotspot_height=0.18,
        raw_text=name,
        variants=[],
    )


def publication(page_text: str) -> Publication:
    pages = [""] * 10
    pages[9] = page_text
    return Publication(
        id="netto-current",
        retailer="Netto",
        title="Netto uge 34",
        source_url="https://netto.test",
        page_count=10,
        page_image_urls=[f"https://img.test/{index}.jpg" for index in range(1, 11)],
        page_texts=pages,
    )


def test_nearby_member_context_is_recall_signal_not_customer_price_truth():
    source = offer()
    filler = " almindelig kampagnetekst" * 8
    page = publication(
        f"Riberhus skiveost 180-300 g 24 kr.{filler} Netto+ + PRIS 20 kr. gælder med Netto+ appen"
    )

    enriched = enrich_ipaper_offers(page, [source])[0]

    assert COVERAGE_SIGNAL in enriched.quality_signals
    assert "[kurv-page-context]" in enriched.raw_text

    pricing = detect_member_pricing(
        retailer="Netto",
        price=24.0,
        normal_price=None,
        text=enriched.raw_text,
    )
    assert pricing is None


def test_page_audit_that_misses_nearby_member_signal_is_forced_to_exact_crop():
    source = offer().model_copy(
        update={"quality_signals": [COVERAGE_SIGNAL]}
    )
    facts = {
        "visible": True,
        "same_offer": True,
        "ordinary_price": 24.0,
        "member_price": None,
        "membership_price_visible": False,
        "member_program": None,
        "member_app": None,
        "unit_price": None,
    }

    reasons = _pricing_sanity_reasons(source, facts)

    assert "page-audit-provider-member-context-unresolved" in reasons


def test_confirmed_member_badge_does_not_retrigger_coverage_crop():
    source = offer().model_copy(
        update={"quality_signals": [COVERAGE_SIGNAL]}
    )
    facts = {
        "visible": True,
        "same_offer": True,
        "ordinary_price": 24.0,
        "member_price": 20.0,
        "membership_price_visible": True,
        "member_program": "Netto+",
        "member_app": "Netto+",
        "unit_price": None,
    }

    reasons = _pricing_sanity_reasons(source, facts)

    assert "page-audit-provider-member-context-unresolved" not in reasons

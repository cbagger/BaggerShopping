from app import member_pricing_v3 as v3
from app.luna_enrichment import review_decision
from app.member_pricing import detect_member_pricing, has_membership_signal
from app.member_pricing_sources import enrich_ipaper_offers
from app.meny_flyer import Offer, Publication


def test_foetex_explicit_plus_price_beats_program_access_text_and_wrong_luna(monkeypatch):
    def wrong_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": None,
            "member_price": 29,
            "member_program": "føtex Plus",
            "member_app": "føtex Plus",
            "requires_activation": False,
            "pricing_confidence": 0.99,
        }

    monkeypatch.setattr(v3, "_luna_override", wrong_luna)
    pricing = detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text=(
            "Salling Seafoodmix, vannameirejer, tunsteak eller -poke 150-300 g. "
            "PLUS PRIS 25,- Gælder kun med føtex Plus appen. "
            "PR. STK. 29,- Pr. kg max. 193,33"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 29
    assert pricing.member_price == 25
    assert pricing.label == "føtex Plus"
    assert pricing.source == "structured-explicit-member-price-v4"


def test_netto_plus_price_beats_nearby_ordinary_price_and_wrong_luna(monkeypatch):
    def wrong_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": None,
            "member_price": 12,
            "member_program": "Netto+",
            "member_app": "Netto+",
            "requires_activation": False,
            "pricing_confidence": 0.99,
        }

    monkeypatch.setattr(v3, "_luna_override", wrong_luna)
    pricing = detect_member_pricing(
        retailer="Netto",
        price=12,
        normal_price=None,
        text=(
            "SPIR plantedrik 1 liter. + PRIS 9,- Gælder kun med Netto+ appen. "
            "12,-"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 12
    assert pricing.member_price == 9
    assert pricing.label == "Netto+"
    assert pricing.source == "structured-explicit-member-price-v4"


def test_365_monthly_member_purchase_uses_provider_campaign_price_with_explicit_nonmember_price():
    pricing = detect_member_pricing(
        retailer="365discount",
        price=18,
        normal_price=None,
        text=(
            "Dolmio sauce Flere varianter 450 g. MÅNEDENS MEDLEMSKØB AUGUST. "
            "Pris ikke-medlem op til 29,95"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 29.95
    assert pricing.member_price == 18
    assert pricing.label == "Coop medlemspris"
    assert pricing.app_name == "Coop-appen"
    assert pricing.source == "structured-membership-context-v4"


def test_broad_membership_phrases_are_source_signals_without_becoming_badges_by_themselves():
    assert has_membership_signal("Månedens medlemskøb august") is True
    assert has_membership_signal("Pris når du er medlem 18,-") is True
    assert has_membership_signal("Kun for medlemmer") is True

    assert detect_member_pricing(
        retailer="365discount",
        price=18,
        normal_price=None,
        text="Månedens medlemskøb august",
    ) is None


def test_page_only_monthly_member_purchase_is_review_evidence_not_customer_badge():
    publication = Publication(
        id="365-week",
        retailer="365discount",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_texts=[
            "Dolmio sauce 450 g. Månedens medlemskøb august 18,- "
            "Pris ikke-medlem op til 29,95"
        ],
    )
    offer = Offer(
        id="dolmio",
        retailer="365discount",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="Dolmio sauce",
        price=18,
        source_url=publication.source_url,
        page_number=1,
        raw_text="Dolmio sauce 450 g.",
        quality_score=0.9,
    )

    enriched = enrich_ipaper_offers(publication, [offer])[0]
    assert "[kurv-page-context]" in enriched.raw_text
    assert detect_member_pricing(
        retailer=enriched.retailer,
        price=enriched.price,
        normal_price=enriched.normal_price,
        text=f"{enriched.product_name} {enriched.raw_text}",
    ) is None

    decision = review_decision(enriched)
    assert decision.review is True
    assert "member-signal-without-safe-price" in decision.reasons


def test_spar_sammen_red_bull_keeps_45_ordinary_and_35_member():
    pricing = detect_member_pricing(
        retailer="SPAR",
        price=45,
        normal_price=None,
        text=(
            "Red Bull Energi 4 x 25 cl. SPAR SAMMEN MEDLEMSPRIS 35,- + pant. "
            "45,- + pant"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 45
    assert pricing.member_price == 35
    assert pricing.label == "SPAR SAMMEN medlemspris"


def test_lidl_variant_dependent_reference_range_stays_without_invented_ordinary_price():
    pricing = detect_member_pricing(
        retailer="Lidl",
        price=10,
        normal_price=None,
        text=(
            "PÅLÆGSSLAGTEREN Pålæg 70-150 g. Med Lidl Plus³ 10.- "
            "13,95-19,95"
        ),
    )

    assert pricing is not None
    assert pricing.member_price == 10
    assert pricing.ordinary_price is None
    assert pricing.label == "Lidl Plus"


def test_activation_remains_explicit_not_just_membership_or_app_access():
    ordinary = detect_member_pricing(
        retailer="Bilka",
        price=15,
        normal_price=None,
        text="Becel PLUS PRIS 12,- Gælder kun med Bilka Plus appen. 15,-",
    )
    assert ordinary is not None
    assert ordinary.requires_activation is False

    activated = detect_member_pricing(
        retailer="MENY",
        price=28,
        normal_price=None,
        text=(
            "Puck hvid ost 28,- MEDLEMSPRIS 22,-. "
            "Kuponen skal aktiveres i appen inden du handler."
        ),
    )
    assert activated is not None
    assert activated.requires_activation is True

from app import member_pricing_v3 as v3
from app.member_pricing import detect_member_pricing
from app.member_pricing_sources import enrich_tjek_offers
from app.meny_flyer import Offer


def _offer(*, identifier: str, retailer: str, name: str, price: float, normal_price=None, page=1):
    return Offer(
        id=f"{identifier}-{page}",
        retailer=retailer,
        publication_id="current-flyer",
        publication_title="Current flyer",
        product_name=name,
        price=price,
        normal_price=normal_price,
        source_url="https://example.test/flyer",
        page_number=page,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text=name,
        hotspot_confidence=0.99,
        quality_score=0.99,
    )


def _pricing(offer):
    return detect_member_pricing(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=f"{offer.product_name} {offer.raw_text}",
        unit_price=offer.unit_price,
    )


def test_tjek_app_price_is_canonical_member_evidence_and_beats_wrong_luna(monkeypatch):
    offer = _offer(
        identifier="seafood",
        retailer="føtex",
        name="Salling Seafoodmix, vannameirejer, tunsteak eller -poke",
        price=29,
        normal_price=166.67,
        page=19,
    )

    detailed = [{
        "id": "seafood",
        "heading": offer.product_name,
        "description": "150-300 g. Flere varianter. Gælder kun med føtex plus appen",
        "price": 29,
        "appPrice": 25,
    }]

    enriched = enrich_tjek_offers([offer], [], detailed)[0]
    assert "member price 25 kr" in enriched.raw_text.casefold()

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
    pricing = _pricing(enriched)

    assert pricing is not None
    assert pricing.ordinary_price == 29
    assert pricing.member_price == 25
    assert pricing.label == "føtex Plus"
    assert pricing.source == "structured-explicit-member-price-v4"


def test_tjek_nested_snake_case_app_price_resolves_netto_plus():
    offer = _offer(
        identifier="spir",
        retailer="Netto",
        name="SPIR plantedrik",
        price=12,
        page=10,
    )

    detailed = [{
        "id": "spir",
        "heading": offer.product_name,
        "description": "1 liter. Gælder kun med Netto+ appen",
        "pricing": {"price": 12, "app_price": 9},
    }]

    enriched = enrich_tjek_offers([offer], [], detailed)[0]
    pricing = _pricing(enriched)

    assert pricing is not None
    assert pricing.ordinary_price == 12
    assert pricing.member_price == 9
    assert pricing.label == "Netto+"


def test_tjek_membership_price_field_is_normalized_for_coop_member_price():
    offer = _offer(
        identifier="dolmio",
        retailer="365discount",
        name="Dolmio sauce",
        price=29.95,
        page=21,
    )

    detailed = [{
        "id": "dolmio",
        "heading": offer.product_name,
        "description": "450 g. Månedens medlemskøb august",
        "price": 29.95,
        "membershipPrice": 18,
    }]

    enriched = enrich_tjek_offers([offer], [], detailed)[0]
    pricing = _pricing(enriched)

    assert pricing is not None
    assert pricing.ordinary_price == 29.95
    assert pricing.member_price == 18
    assert pricing.label == "Coop medlemspris"

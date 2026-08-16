from app.member_pricing import detect_member_pricing
from app.meny_flyer import Offer


def test_keeps_ordinary_price_and_extracts_generic_member_price():
    pricing = detect_member_pricing(
        retailer="MENY",
        price=16,
        normal_price=None,
        text="Pågen gifflar 16 kr. MEDLEMSPRIS 9,95",
    )

    assert pricing is not None
    assert pricing.ordinary_price == 16
    assert pricing.member_price == 9.95
    assert pricing.label == "MENY medlemspris"
    assert pricing.app_name == "MENY-appen"


def test_restores_visible_ordinary_price_when_provider_primary_is_member_price():
    pricing = detect_member_pricing(
        retailer="MENY",
        price=9.95,
        normal_price=24,
        text="Almindelig pris 16 kr. Medlemspris 9,95 kr.",
    )

    assert pricing is not None
    assert pricing.primary_price_was_member is True
    assert pricing.ordinary_price == 16
    assert pricing.member_price == 9.95


def test_member_only_price_does_not_invent_ordinary_price():
    pricing = detect_member_pricing(
        retailer="SPAR",
        price=12.95,
        normal_price=None,
        text="Kundeklubpris 12,95 kr.",
    )

    assert pricing is not None
    assert pricing.ordinary_price is None
    assert pricing.member_price == 12.95
    assert pricing.label == "Kundeklubpris"


def test_preserves_explicit_membership_program_name():
    pricing = detect_member_pricing(
        retailer="Lidl",
        price=18,
        normal_price=None,
        text="Lidl Plus 14,95 kr. Normal pris 18 kr.",
    )

    assert pricing is not None
    assert pricing.ordinary_price == 18
    assert pricing.member_price == 14.95
    assert pricing.label == "Lidl Plus"
    assert pricing.app_name == "Lidl Plus"


def test_plain_discount_without_membership_marker_is_not_reclassified():
    assert detect_member_pricing(
        retailer="Bilka",
        price=10,
        normal_price=16,
        text="Skarp pris 10 kr. Før 16 kr.",
    ) is None


def test_offer_payload_separates_member_price_from_main_price():
    offer = Offer(
        id="member-1",
        retailer="MENY",
        publication_id="week-34",
        publication_title="MENY uge 34",
        product_name="Pågen gifflar",
        price=9.95,
        normal_price=24,
        source_url="https://example.test",
        raw_text="Pågen gifflar Almindelig pris 16 kr. Medlemspris 9,95 kr.",
    )

    payload = offer.model_dump()

    assert payload["price"] == 16
    assert payload["normal_price"] == 24
    assert payload["member_price"] == 9.95
    assert payload["member_price_label"] == "MENY medlemspris"
    assert payload["member_price_app"] == "MENY-appen"
    assert payload["member_price_requires_activation"] is True

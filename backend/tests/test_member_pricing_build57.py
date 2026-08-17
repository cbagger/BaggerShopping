from app.luna_enrichment import review_decision
from app.member_pricing import detect_member_pricing, has_membership_signal
from app.meny_flyer import Offer


def test_lidl_plus_with_superscript_keeps_product_and_unit_prices_separate():
    text = (
        "Grønne kernefri vindruer Klasse 1. 500 g. Pr. kg 30,00 "
        "Med Lidl Plus³ 12.- Pr. kg 24,00 15.-"
    )
    assert has_membership_signal(text) is True
    pricing = detect_member_pricing(
        retailer="Lidl", price=15, normal_price=None, text=text,
    )
    assert pricing is not None
    assert pricing.ordinary_price == 15
    assert pricing.member_price == 12
    assert pricing.label == "Lidl Plus"


def test_lidl_member_primary_with_variant_dependent_reference_range_does_not_invent_one_ordinary_price():
    text = (
        "PÅLÆGSSLAGTEREN Pålæg 70-150 g. Pr. kg maks. 142,86 "
        "Maks. 6 pk. pr. kunde pr. dag. Med Lidl Plus³ 10.- 13,95-19,95"
    )
    pricing = detect_member_pricing(
        retailer="Lidl", price=10, normal_price=None, text=text,
    )
    assert pricing is not None
    assert pricing.member_price == 10
    assert pricing.ordinary_price is None
    assert pricing.label == "Lidl Plus"


def test_netto_symbol_plus_price_is_a_strong_member_price_role():
    text = (
        "Jakobsens honning SPOT 250-425 g. Pr. kg max. 128,00 "
        "+ PRIS 28:- Pr. kg max. 112,00 Gælder kun med Netto+ appen"
    )
    assert has_membership_signal(text) is True
    pricing = detect_member_pricing(
        retailer="Netto", price=32, normal_price=None, text=text,
    )
    assert pricing is not None
    assert pricing.ordinary_price == 32
    assert pricing.member_price == 28
    assert pricing.label == "Netto+"


def test_netto_app_without_explicit_member_amount_is_sent_to_luna_not_misclassified():
    text = (
        "The Wild Life, My Way, Mucho Mas eller La Belle Angele 75 cl. "
        "Pr. liter 66,67 Pr. liter 56,00 Gælder kun med Netto+ appen | "
        "pricing price 50 kr"
    )
    assert has_membership_signal(text) is True
    assert detect_member_pricing(
        retailer="Netto", price=50, normal_price=None, text=text,
    ) is None

    offer = Offer(
        id="wine", retailer="Netto", publication_id="week",
        publication_title="Netto uge", product_name="The Wild Life, My Way, Mucho Mas eller La Belle Angele",
        price=50, source_url="https://example.test", page_number=18,
        raw_text=text, quality_score=0.9, variant_confidence=0.93,
    )
    decision = review_decision(offer)
    assert decision.review is True
    assert "member-signal-without-safe-price" in decision.reasons


def test_plain_unit_price_sequence_is_not_promoted_to_member_price():
    assert detect_member_pricing(
        retailer="Netto",
        price=50,
        normal_price=None,
        text="Vin 75 cl. Pr. liter 66,67 Pr. liter 56,00 50 kr.",
    ) is None

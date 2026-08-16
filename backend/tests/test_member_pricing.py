from app.member_pricing import detect_member_pricing
from app.member_pricing_sources import enrich_ipaper_offers, enrich_schwarz_publication
from app.meny_flyer import Offer, Publication


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


def test_meny_screenshot_15_and_895_is_classified_correctly():
    pricing = detect_member_pricing(
        retailer="MENY",
        price=15,
        normal_price=None,
        text=(
            "PÅGEN KANEL GIFFLAR ELLER VANILLAS 220-300 g. PR. POSE 15,- "
            "MEDLEMSPRIS* PR. POSE 8,95 Max. 4 poser pr. kunde"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 15
    assert pricing.member_price == 8.95
    assert pricing.label == "MENY medlemspris"


def test_foetex_screenshot_never_promotes_kg_price_to_ordinary_price():
    pricing = detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text=(
            "Salling Seafoodmix, vannameirejer, tunsteak eller -poke 150-300 g. "
            "plus pris 25,- Gælder kun med føtex plus appen 29,- "
            "Pr. kg max. 166,67"
        ),
    )

    assert pricing is not None
    assert pricing.ordinary_price == 29
    assert pricing.member_price == 25
    assert pricing.label == "føtex Plus"
    assert pricing.app_name == "føtex Plus"


def test_foetex_does_not_blindly_pair_program_with_bad_provider_pre_price():
    pricing = detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text="Salling Seafoodmix føtex Plus Pr. kg max. 166,67",
    )

    assert pricing is None


def test_lidl_screenshot_primary_10_is_member_and_1995_is_ordinary():
    pricing = detect_member_pricing(
        retailer="Lidl",
        price=10,
        normal_price=None,
        text="LURPAK Smør eller smørbar 200 g. Med Lidl Plus -49% 10,- 19,95",
    )

    assert pricing is not None
    assert pricing.primary_price_was_member is True
    assert pricing.ordinary_price == 19.95
    assert pricing.member_price == 10
    assert pricing.label == "Lidl Plus"


def test_explicit_plus_price_beats_later_program_name_and_other_prices():
    pricing = detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text="plus pris 25,- føtex Plus 29,- pr. kg max. 166,67",
    )

    assert pricing is not None
    assert pricing.ordinary_price == 29
    assert pricing.member_price == 25


def test_ipaper_page_text_supplies_meny_member_price_context():
    publication = Publication(
        id="meny-34",
        retailer="MENY",
        title="MENY uge 34",
        source_url="https://example.test",
        page_count=1,
        page_texts=[
            "Andre varer. PÅGEN KANEL GIFFLAR ELLER VANILLAS PR. POSE 15,- "
            "MEDLEMSPRIS PR. POSE 8,95 Max. 4 poser pr. kunde. Andre varer."
        ],
    )
    offer = Offer(
        id="gifflar",
        retailer="MENY",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="Pågen kanel gifflar eller vanillas",
        price=15,
        source_url=publication.source_url,
        page_number=1,
        raw_text="220-300 g.",
    )

    enriched = enrich_ipaper_offers(publication, [offer])[0]
    payload = enriched.model_dump()

    assert payload["price"] == 15
    assert payload["member_price"] == 8.95
    assert payload["member_price_label"] == "MENY medlemspris"


def test_schwarz_page_keywords_supply_lidl_plus_price_context():
    publication = Publication(
        id="lidl-34",
        retailer="Lidl",
        title="Lidl uge 34",
        source_url="https://example.test",
        page_count=1,
    )
    publication.structured_offers = [Offer(
        id="lurpak",
        retailer="Lidl",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="LURPAK Smør eller smørbar",
        price=10,
        source_url=publication.source_url,
        page_number=1,
        raw_text="200 g.",
    )]
    payload = {
        "flyer": {
            "pages": [{
                "keyWords": "LURPAK Smør eller smørbar 200 g Med Lidl Plus -49% 10,- 19,95",
                "links": [],
            }],
            "products": {},
        }
    }

    offer = enrich_schwarz_publication(publication, payload).structured_offers[0]
    dumped = offer.model_dump()

    assert dumped["price"] == 19.95
    assert dumped["member_price"] == 10
    assert dumped["member_price_label"] == "Lidl Plus"


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

from app.luna_enrichment import review_decision
from app.member_pricing import detect_member_pricing
from app.member_pricing_sources import enrich_ipaper_offers, enrich_schwarz_publication, enrich_tjek_offers
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


def test_explicit_plus_price_preserves_program_name():
    pricing = detect_member_pricing(
        retailer="Lidl",
        price=18,
        normal_price=None,
        text="Lidl Plus PLUS PRIS 14,95 kr. Normal pris 18 kr.",
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


def test_foetex_never_promotes_kg_price_to_ordinary_price():
    pricing = detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text=(
            "Salling Seafoodmix 150-300 g. plus pris 25,- "
            "Gælder med føtex Plus appen 29,- Pr. kg max. 166,67"
        ),
    )
    assert pricing is not None
    assert pricing.ordinary_price == 29
    assert pricing.member_price == 25
    assert pricing.label == "føtex Plus"


def test_foetex_does_not_blindly_pair_program_with_bad_pre_price():
    assert detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=166.67,
        text="Salling Seafoodmix føtex Plus Pr. kg max. 166,67",
    ) is None


def test_lidl_primary_10_is_member_and_1995_is_ordinary_when_plus_price_is_explicit():
    pricing = detect_member_pricing(
        retailer="Lidl",
        price=10,
        normal_price=None,
        text="LURPAK 200 g. Lidl Plus PLUS PRIS 10,- Normal pris 19,95",
    )
    assert pricing is not None
    assert pricing.primary_price_was_member is True
    assert pricing.ordinary_price == 19.95
    assert pricing.member_price == 10
    assert pricing.label == "Lidl Plus"


def test_coop_membership_fee_200_never_becomes_product_price():
    pricing = detect_member_pricing(
        retailer="365discount",
        price=200,
        normal_price=None,
        text=(
            "Mokai COOP MEDLEMSPRIS 10,- Pris ikke-medlem 13,00. "
            "Et Coop medlemskab koster et engangsbeløb på 200 kr."
        ),
    )
    assert pricing is not None
    assert pricing.member_price == 10
    assert pricing.ordinary_price == 13
    assert pricing.label == "Coop medlemspris"
    assert pricing.app_name == "Coop-appen"


def test_spar_sammen_keeps_15_as_ordinary_and_12_as_member():
    pricing = detect_member_pricing(
        retailer="SPAR",
        price=12,
        normal_price=None,
        text="Stay Strong SPAR SAMMEN MEDLEMSPRIS 12:- PR. STK. 15:-",
    )
    assert pricing is not None
    assert pricing.member_price == 12
    assert pricing.ordinary_price == 15
    assert pricing.label == "SPAR SAMMEN medlemspris"


def test_bilka_plus_139_and_149_remains_correct():
    pricing = detect_member_pricing(
        retailer="Bilka",
        price=149,
        normal_price=None,
        text="Helbønnemarked Bilka Plus PLUS PRIS 139,- 149,-",
    )
    assert pricing is not None
    assert pricing.member_price == 139
    assert pricing.ordinary_price == 149
    assert pricing.label == "Bilka Plus"


def test_membership_app_alone_does_not_mean_offer_must_be_activated():
    pricing = detect_member_pricing(
        retailer="Bilka",
        price=149,
        normal_price=None,
        text="Bilka Plus app. PLUS PRIS 139,- 149,-. Kupon i appen.",
    )
    assert pricing is not None
    assert pricing.requires_activation is False


def test_explicit_activation_text_enables_activation_reminder():
    pricing = detect_member_pricing(
        retailer="MENY",
        price=15,
        normal_price=None,
        text="15 kr. MEDLEMSPRIS 8,95. Kuponen skal aktiveres i MENY-appen.",
    )
    assert pricing is not None
    assert pricing.requires_activation is True


def test_ipaper_page_neighbour_context_is_not_customer_facing_without_luna():
    publication = Publication(
        id="meny-34",
        retailer="MENY",
        title="MENY uge 34",
        source_url="https://example.test",
        page_count=1,
        page_texts=[
            "GM juice 16,-. PÅGEN GIFFLAR PR. POSE 15,- MEDLEMSPRIS 8,95. "
            "Gestus pommes 20,-."
        ],
    )
    unrelated = Offer(
        id="juice",
        retailer="MENY",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="GM juice",
        price=16,
        source_url=publication.source_url,
        page_number=1,
        raw_text="GM juice",
        quality_score=0.9,
    )
    enriched = enrich_ipaper_offers(publication, [unrelated])[0]
    dumped = enriched.model_dump()
    assert dumped["price"] == 16
    assert "member_price" not in dumped


def test_page_context_member_signal_is_sent_to_luna_gate_instead_of_badge():
    offer = Offer(
        id="page-member",
        retailer="MENY",
        publication_id="week",
        publication_title="Uge",
        product_name="Pågen gifflar",
        price=15,
        source_url="https://example.test",
        page_number=1,
        raw_text="[kurv-page-context] Pågen 15,- MEDLEMSPRIS 8,95 [/kurv-page-context]",
        quality_score=0.9,
    )
    assert detect_member_pricing(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=f"{offer.product_name} {offer.raw_text}",
    ) is None
    decision = review_decision(offer)
    assert decision.review is True
    assert "member-signal-without-safe-price" in decision.reasons


def test_schwarz_page_keywords_are_review_evidence_not_automatic_badge():
    publication = Publication(
        id="lidl-34", retailer="Lidl", title="Lidl uge 34",
        source_url="https://example.test", page_count=1,
    )
    publication.structured_offers = [Offer(
        id="lurpak", retailer="Lidl", publication_id=publication.id,
        publication_title=publication.title, product_name="LURPAK Smør eller smørbar",
        price=10, source_url=publication.source_url, page_number=1, raw_text="200 g.",
        quality_score=0.9,
    )]
    payload = {"flyer": {"pages": [{
        "keyWords": "LURPAK Smør 200 g Lidl Plus 10,- 19,95", "links": [],
    }], "products": {}}}
    offer = enrich_schwarz_publication(publication, payload).structured_offers[0]
    dumped = offer.model_dump()
    assert dumped["price"] == 10
    assert "member_price" not in dumped
    assert review_decision(offer).review is True


def test_tjek_structured_member_fields_are_attached_to_exact_offer():
    publication = Publication(
        id="catalog", retailer="365discount", title="Uge 34",
        source_url="https://example.test", page_count=1,
    )
    offer = Offer(
        id="drink-1", retailer="365discount", publication_id=publication.id,
        publication_title=publication.title, product_name="Mokai",
        price=13, source_url=publication.source_url, page_number=1,
        raw_text="Mokai", quality_score=0.9,
    )
    detailed = [{
        "id": "drink",
        "heading": "Mokai",
        "memberPrice": 10,
        "regularPrice": 13,
        "membershipText": "Coop medlemspris",
    }]
    enriched = enrich_tjek_offers([offer], [], detailed)[0]
    pricing = detect_member_pricing(
        retailer=enriched.retailer,
        price=enriched.price,
        normal_price=enriched.normal_price,
        text=f"{enriched.product_name} {enriched.raw_text}",
    )
    assert pricing is not None
    assert pricing.member_price == 10
    assert pricing.ordinary_price == 13


def test_offer_payload_requires_explicit_activation_language():
    from app.offer_serialization import customer_offer_payload

    offer = Offer(
        id="member-1", retailer="MENY", publication_id="week-34",
        publication_title="MENY uge 34", product_name="Pågen gifflar",
        price=9.95, normal_price=24, source_url="https://example.test",
        raw_text=(
            "Pågen gifflar Almindelig pris 16 kr. Medlemspris 9,95 kr. "
            "Kuponen skal aktiveres i MENY-appen."
        ),
    )
    payload = customer_offer_payload(offer)
    assert payload["price"] == 16
    assert payload["normal_price"] == 24
    assert payload["member_price"] == 9.95
    assert payload["member_price_label"] == "MENY medlemspris"
    assert payload["member_price_app"] == "MENY-appen"
    assert payload["member_price_requires_activation"] is True

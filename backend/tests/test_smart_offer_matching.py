from app.meny_flyer import Offer, OfferVariant, Publication
from app.mobile_offers import match_items_to_publications


def publication(*offers: Offer) -> Publication:
    return Publication(
        id="pub-current",
        retailer="MENY",
        title="Aktuel avis",
        valid_from="13.08.2026",
        valid_until="19.08.2026",
        status="current",
        source_url="https://example.com/flyer",
        page_count=1,
        page_image_urls=["https://example.com/page.jpg"],
        structured_offers=list(offers),
    )


def offer(
    *,
    offer_id: str,
    name: str,
    price: float,
    variants: list[str] | None = None,
    retailer: str = "MENY",
) -> Offer:
    return Offer(
        id=offer_id,
        retailer=retailer,
        publication_id="pub-current",
        publication_title="Aktuel avis",
        valid_from="13.08.2026",
        valid_until="19.08.2026",
        product_name=name,
        price=price,
        source_url="https://example.com/flyer",
        raw_text="",
        safe_to_add=True,
        variants=[
            OfferVariant(id=f"{offer_id}-{index}", name=variant)
            for index, variant in enumerate(variants or [], 1)
        ],
    )


def matched_names(groups: list[dict], item_name: str) -> list[str]:
    group = next(group for group in groups if group["item_name"] == item_name)
    return [candidate["product_name"] for candidate in group["offers"]]


def test_specific_milk_variant_only_returns_matching_campaign_variant():
    dairy = offer(
        offer_id="milk",
        name="Arla mælk",
        price=11.95,
        variants=["Arla Sødmælk 3,5%", "Arla Letmælk 1,5%"],
    )

    groups = match_items_to_publications(["Sødmælk"], [publication(dairy)])

    assert len(groups) == 1
    assert matched_names(groups, "Sødmælk") == ["Arla mælk"]
    returned_variants = groups[0]["offers"][0]["variants"]
    assert [variant["name"] for variant in returned_variants] == ["Arla Sødmælk 3,5%"]


def test_short_item_name_does_not_match_inside_unrelated_compound():
    cold_cuts = offer(
        offer_id="cold-cuts",
        name="Dansk pålæg",
        price=20.0,
    )

    groups = match_items_to_publications(["Æg"], [publication(cold_cuts)])

    assert groups == []


def test_missing_brand_can_still_surface_same_core_product_for_manual_approval():
    pizza = offer(
        offer_id="pizza-dough",
        name="Frisk pizzadej",
        price=10.0,
        retailer="REMA 1000",
    )

    groups = match_items_to_publications(["Coop pizzadej"], [publication(pizza)])

    assert len(groups) == 1
    assert groups[0]["offers"][0]["retailer"] == "REMA 1000"


def test_pet_food_is_not_suggested_for_meat_item():
    pet = offer(
        offer_id="pet-food",
        name="Whiskas kattemad",
        price=29.0,
        variants=["Whiskas med oksekød"],
    )

    groups = match_items_to_publications(["Oksekød"], [publication(pet)])

    assert groups == []


def test_expired_publications_are_ignored_and_results_are_limited():
    current = publication(*[
        offer(offer_id=f"coffee-{index}", name=f"Kaffe variant {index}", price=20.0 + index)
        for index in range(6)
    ])
    expired = publication(offer(offer_id="cheap-coffee", name="Kaffe", price=1.0))
    expired.status = "expired"

    groups = match_items_to_publications(["Kaffe"], [current, expired])

    assert len(groups) == 1
    assert len(groups[0]["offers"]) == 4
    assert all(candidate["id"] != "cheap-coffee" for candidate in groups[0]["offers"])

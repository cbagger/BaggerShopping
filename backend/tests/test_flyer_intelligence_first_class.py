from app.flyer_intelligence import box_from_polygon, couple_offers, extract_variants
from app.meny_flyer import Offer


def _offer(identity: str, *, x: float) -> Offer:
    return Offer(
        id=identity,
        retailer="Netto",
        publication_id="week-34",
        publication_title="Uge 34",
        product_name="Samme kampagnetekst",
        price=20,
        source_url="https://example.test",
        raw_text="Samme kampagnetekst 20 kr",
        page_number=2,
        hotspot_x=x,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        hotspot_confidence=0.97,
        quality_source="tjek-catalog",
    )


def test_small_provider_polygon_keeps_real_marker_recall():
    box = box_from_polygon(
        [[0.1, 0.1], [0.105, 0.1], [0.105, 0.12], [0.1, 0.12]],
        source="tjek-polygon",
    )

    assert box is not None
    assert box.width == 0.005
    assert box.height == 0.02


def test_nearby_same_price_offers_are_not_coupled_without_near_identical_geometry():
    first = _offer("first", x=0.10)
    nearby = _offer("nearby", x=0.15)

    result = couple_offers([first, nearby])

    assert [offer.id for offer in result] == ["first", "nearby"]


def test_public_extract_variants_is_variant_extractor_v2_behavior():
    variants = extract_variants(
        "bread",
        "Schulstad brød",
        payload={
            "products": [
                {"name": "Schulstad Det Gode Solsikkerugbrød"},
                {"name": "Schulstad Levebrød Sandwich"},
            ],
            "ocr": {
                "choices": [
                    {"name": "Lambi toiletpapir"},
                ]
            },
        },
    )

    assert [variant.name for variant in variants] == [
        "Schulstad Det Gode Solsikkerugbrød",
        "Schulstad Levebrød Sandwich",
    ]
    assert all(variant.source == "structured-products" for variant in variants)

import asyncio

import pytest

from app.flyer_intelligence import (
    assess_quality,
    box_from_mapping,
    box_from_polygon,
    couple_offers,
    extract_ocr_regions,
    extract_variants,
    learned_adjustment,
    load_feedback_store,
    save_feedback_store,
    text_for_hotspot,
)
from app.meny_flyer import Offer, OfferVariant
from app import mobile_offers


def offer(**updates) -> Offer:
    values = {
        "id": "offer-1",
        "retailer": "Netto",
        "publication_id": "week-34",
        "publication_title": "Uge 34",
        "product_name": "Schulstad brød",
        "price": 12,
        "source_url": "https://netto.test",
        "page_number": 10,
        "hotspot_x": 0.1,
        "hotspot_y": 0.2,
        "hotspot_width": 0.3,
        "hotspot_height": 0.2,
        "raw_text": "Schulstad brød. Frit valg.",
        "safe_to_add": True,
        "variants": [OfferVariant(id="a", name="Schulstad Levebrød")],
        "hotspot_confidence": 0.97,
        "variant_confidence": 0.98,
        "quality_score": 0.94,
        "quality_source": "tjek-catalog",
    }
    values.update(updates)
    return Offer(**values)


def test_layout_engine_normalizes_percent_geometry_and_small_rounding_overshoot():
    box = box_from_mapping(
        {"bounds": {"left": "80", "top": "90", "w": "20.01", "h": "10.01"}},
        source="ipaper-marker",
    )
    assert box is not None
    assert (box.x, box.y) == pytest.approx((0.8, 0.9))
    assert (box.width, box.height) == pytest.approx((0.2, 0.1))
    assert box.confidence >= 0.9


def test_layout_engine_converts_tjek_portrait_polygon_to_page_coordinates():
    box = box_from_polygon(
        [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]],
        vertical_scale=2 ** 0.5,
        source="tjek-polygon",
    )
    assert box is not None
    assert box.x == pytest.approx(0.1)
    assert box.y == pytest.approx(0.2 / (2 ** 0.5))
    assert box.height == pytest.approx(0.6 / (2 ** 0.5))


def test_ocr_layout_engine_attaches_only_text_from_the_selected_offer_region():
    regions = extract_ocr_regions({"textBlocks": [
        {
            "text": "Schulstad Det Gode Solsikkerugbrød eller Levebrød Sandwich",
            "confidence": 96,
            "bounds": {"left": 10, "top": 20, "width": 35, "height": 12},
        },
        {
            "text": "Lambi toiletpapir 20 kr.",
            "confidence": 98,
            "bounds": {"left": 60, "top": 70, "width": 30, "height": 12},
        },
    ]})
    bread = box_from_mapping(
        {"x": 0.08, "y": 0.18, "width": 0.40, "height": 0.18},
        source="tjek-polygon",
    )
    assert bread is not None
    context = text_for_hotspot(regions, bread)
    assert "Schulstad" in context
    assert "Lambi" not in context


def test_variant_engine_prefers_structured_products_and_keeps_all_choices():
    variants = extract_variants(
        "bread", "Schulstad brød", "Udvalgte varianter. 470-1080 g.",
        payload={"products": [
            {"name": "Schulstad Det Gode Solsikkerugbrød"},
            {"name": "Schulstad Levebrød Sandwich"},
            {"name": "Schulstad Signaturbrød"},
        ]},
    )
    assert [value.name for value in variants] == [
        "Schulstad Det Gode Solsikkerugbrød",
        "Schulstad Levebrød Sandwich",
        "Schulstad Signaturbrød",
    ]
    assert all(value.source == "structured-products" for value in variants)
    assert min(value.confidence for value in variants) >= 0.95


def test_variant_engine_restores_danish_relative_alternatives():
    assert [value.name for value in extract_variants(
        "bacon", "Tulip bacon i skiver eller i tern",
    )] == ["Tulip bacon i skiver", "Tulip bacon i tern"]
    assert [value.name for value in extract_variants(
        "turkey", "Kalkunoverlår eller -schnitzel af brystfilet",
    )] == ["Kalkunoverlår", "Kalkunschnitzel af brystfilet"]


def test_quality_engine_explains_confident_and_incomplete_hotspots():
    box = box_from_mapping(
        {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.15},
        source="tjek-polygon",
    )
    variants = extract_variants("paper", "Lambi toiletpapir eller køkkenruller")
    strong = assess_quality(
        heading="Lambi toiletpapir eller køkkenruller",
        raw_text="Lambi. Frit valg. 20 kr.",
        price=20,
        box=box,
        variants=variants,
        structured=True,
        has_crop=True,
    )
    weak = assess_quality(
        heading="Ukendt vare", raw_text="", price=None, box=None,
        variants=[], structured=False, has_crop=False,
    )
    assert strong.score > 0.85
    assert strong.issues == []
    assert weak.score < 0.40
    assert {"missing-price", "missing-hotspot", "missing-variants"} <= set(weak.issues)


def test_hotspot_variant_coupling_collapses_source_duplicates_but_not_prices():
    first = offer()
    duplicate = offer(
        id="offer-1-copy",
        hotspot_x=0.11,
        variants=[OfferVariant(id="b", name="Schulstad Signaturbrød")],
    )
    other_price = offer(id="offer-price", price=15)
    coupled = couple_offers([first, duplicate, other_price])
    assert len(coupled) == 2
    campaign = next(value for value in coupled if value.price == 12)
    assert [value.name for value in campaign.variants] == [
        "Schulstad Levebrød", "Schulstad Signaturbrød",
    ]
    assert "coupled-source-rows" in campaign.quality_signals


def test_learning_store_applies_cautious_source_level_adjustment(tmp_path):
    path = tmp_path / "quality.json"
    store = {
        "version": 1,
        "sources": {"netto|tjek-catalog": {
            "correct": 8, "wrong_position": 2, "wrong_variants": 1,
        }},
        "reports": [],
    }
    save_feedback_store(path, store)
    loaded = load_feedback_store(path)
    adjustment = learned_adjustment(loaded, "Netto", "tjek-catalog")
    assert 0 < adjustment.score <= 0.08
    assert adjustment.position_reports == 2
    assert adjustment.variant_reports == 1


def test_quality_feedback_endpoint_persists_anonymous_learning_signal(monkeypatch, tmp_path):
    path = tmp_path / "quality.json"
    monkeypatch.setattr(mobile_offers, "_QUALITY_STORE_PATH", str(path))
    request = mobile_offers.FlyerQualityFeedbackRequest(
        publication_id="week-34", offer_id="bread", retailer="Netto",
        quality_source="tjek-catalog", decision="wrong_variants", page_number=10,
    )
    response = asyncio.run(mobile_offers.flyer_quality_feedback(request))
    store = load_feedback_store(path)
    assert response["ok"] is True
    assert store["sources"]["netto|tjek-catalog"]["wrong_variants"] == 1
    assert store["reports"][0]["offer_id"] == "bread"

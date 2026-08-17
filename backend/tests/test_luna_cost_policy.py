from __future__ import annotations

import json

from app import luna_cost_policy as policy
from app import luna_enrichment as luna
from app.meny_flyer import Offer


def _isolated(monkeypatch, tmp_path, **config_updates):
    config = tmp_path / "config.json"
    store = tmp_path / "store.json"
    value = {
        "enabled": True,
        "apply_results": True,
        "min_apply_confidence": 0.96,
        **config_updates,
    }
    config.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)


def _offer(*, price=15, raw_text="provider text", product_name="Becel flydende"):
    return Offer(
        id="offer",
        retailer="Bilka",
        publication_id="pub",
        publication_title="Uge 34",
        product_name=product_name,
        price=price,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        raw_text=raw_text,
        variant_confidence=0.62,
        quality_score=0.97,
    )


def _facts(**updates):
    value = {
        "visible": True,
        "product_name": "Becel flydende",
        "brand": "Becel",
        "ordinary_price": 15,
        "member_price": None,
        "member_program": None,
        "member_app": None,
        "requires_activation": False,
        "before_price": None,
        "unit_price": "30 kr/l",
        "package_size": "500 ml",
        "multiple_products": False,
        "variants": ["Original"],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.90,
        "needs_crop_verification": False,
    }
    value.update(updates)
    return value


def test_plain_safe_price_does_not_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = _facts()
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False
    assert policy._balanced_crop_reasons(offer, facts, False) == []


def test_visual_only_member_price_requires_independent_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(raw_text="Becel flydende 500 ml")
    facts = _facts(
        ordinary_price=15,
        member_price=12,
        member_program="Bilka Plus",
        pricing_confidence=0.99,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    reasons = policy._balanced_crop_reasons(offer, facts, True)
    assert "page-audit-new-member-price-verification" in reasons


def test_provider_member_evidence_allows_high_confidence_member_price(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(raw_text="Bilka Plus pris 12 kr. Normalpris 15 kr.")
    facts = _facts(
        ordinary_price=15,
        member_price=12,
        member_program="Bilka Plus",
        pricing_confidence=0.99,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False


def test_variant_only_model_crop_is_suppressed_when_pricing_is_safe(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(product_name="Castello dessertost")
    facts = _facts(
        product_name="Castello dessertost",
        brand="Castello",
        ordinary_price=15,
        member_price=None,
        multiple_products=True,
        variants=["Saga", "Creamy White", "Creamy Blue"],
        variant_confidence=0.82,
        pricing_confidence=0.99,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False
    assert policy._balanced_crop_reasons(offer, facts, False) == []


def test_missing_member_amount_still_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(raw_text="Bilka Plus")
    facts = _facts(
        member_price=None,
        member_program="Bilka Plus",
        pricing_confidence=0.90,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    reasons = policy._balanced_crop_reasons(offer, facts, True)
    assert "page-audit-member-price-missing" in reasons


def test_low_confidence_member_price_still_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(raw_text="Bilka Plus pris")
    facts = _facts(
        ordinary_price=15,
        member_price=12,
        member_program="Bilka Plus",
        pricing_confidence=0.82,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-member-price-low-confidence" in policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_provider_price_conflict_still_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(price=15)
    facts = _facts(ordinary_price=20, member_price=12, pricing_confidence=0.99)
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-provider-price-conflict" in policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_invalid_member_relation_still_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(raw_text="Bilka Plus")
    facts = _facts(ordinary_price=12, member_price=15, member_program="Bilka Plus")
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-price-role-conflict" in policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_invisible_target_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = _facts(visible=False)
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-target-not-visible" in policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_status_documents_current_luna_prices_and_quality_mode(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    status = policy.status_payload()
    assert status["page_mode"] == "rich-page-audit-cost-balanced-v3"
    assert status["page_image_detail"] == "high"
    assert status["page_reasoning_effort"] == "low"
    assert status["proactive_variant_crops"] is False
    assert status["visual_only_member_price_requires_crop"] is True
    assert status["current_luna_input_usd_per_million"] == 0.20
    assert status["current_luna_output_usd_per_million"] == 1.20


def test_default_config_keeps_monthly_guard():
    assert luna.DEFAULT_CONFIG["monthly_budget_dkk"] == 20.0
    assert luna.DEFAULT_CONFIG["proactive_variant_crops"] is False

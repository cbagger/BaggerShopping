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
        "selective_variant_crops": True,
        "variant_crop_confidence_threshold": 0.80,
        "variant_crop_max_monthly_dkk": 5.0,
        "input_usd_per_million": 0.20,
        "output_usd_per_million": 1.20,
        "usd_to_dkk": 7.0,
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


class _Crop:
    def __init__(self, product_name, reasons, page=1):
        self.reasons = tuple(reasons)
        self.offer = _offer(product_name=product_name)
        self.offer.page_number = page


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


def test_castello_like_rich_variants_do_not_crop_at_093(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(product_name="Castello dessertost")
    facts = _facts(
        product_name="Castello dessertost",
        brand="Castello",
        multiple_products=True,
        variants=["Saga", "Creamy White", "Creamy Blue"],
        variant_confidence=0.93,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False


def test_iskasse_like_two_concrete_variants_do_not_crop_at_088(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(product_name="Iskasse")
    facts = _facts(
        product_name="Iskasse",
        multiple_products=True,
        variants=["Mini ananas ispinde", "Mini mix"],
        variant_confidence=0.88,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False


def test_actimel_like_multi_product_without_variants_gets_enrichment_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(product_name="Actimel 12-pak", price=29)
    facts = _facts(
        product_name="Actimel 12-pak",
        ordinary_price=29,
        multiple_products=True,
        variants=[],
        variant_confidence=0.55,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    reasons = policy._balanced_crop_reasons(offer, facts, True)
    assert reasons == ["page-audit-variant-enrichment"]
    assert policy.is_variant_only_crop(reasons) is True


def test_multi_product_with_weak_named_variants_still_gets_enrichment_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(product_name="Mix")
    facts = _facts(
        product_name="Mix",
        multiple_products=True,
        variants=["A", "B"],
        variant_confidence=0.75,
    )
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-variant-enrichment" in policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_selective_variant_crops_can_be_disabled_without_affecting_pricing(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path, selective_variant_crops=False)
    offer = _offer(product_name="Actimel 12-pak", price=29)
    variant_only = _facts(
        product_name="Actimel 12-pak",
        ordinary_price=29,
        multiple_products=True,
        variants=[],
        variant_confidence=0.20,
        needs_crop_verification=True,
    )
    assert policy._balanced_server_needs_crop(offer, variant_only, 0.96) is False

    member = _facts(member_price=12, member_program="Bilka Plus")
    assert policy._balanced_server_needs_crop(_offer(), member, 0.96) is True


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


def test_pricing_crops_sort_before_variant_only_crops(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    variant = _Crop("Actimel", ["page-audit-variant-enrichment"])
    pricing = _Crop("Becel", ["page-audit-new-member-price-verification"])
    mixed = _Crop(
        "Mixed",
        ["page-audit-provider-price-conflict", "page-audit-variant-enrichment"],
    )
    ordered = policy.sort_crop_candidates([variant, pricing, mixed])
    assert ordered[-1] is variant
    assert policy.is_variant_only_crop(variant) is True
    assert policy.is_variant_only_crop(mixed) is False


def test_variant_crop_monthly_slice_stops_optional_work(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path, variant_crop_max_monthly_dkk=0.004)
    luna.save_store({
        "records": {
            "v": {
                "status": "completed",
                "analysis_level": "crop",
                "reasons": ["page-audit-variant-enrichment"],
                "usage": {"input_tokens": 1964, "output_tokens": 193},
            },
            "p": {
                "status": "completed",
                "analysis_level": "crop",
                "reasons": ["page-audit-new-member-price-verification"],
                "usage": {"input_tokens": 5000, "output_tokens": 500},
            },
        },
        "pricing_index": {},
        "usage": {},
        "events": [],
    })
    assert policy.variant_crop_spend_dkk() == 0.004371
    assert policy.variant_crop_budget_allows() is False


def test_status_documents_quality_first_mode(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    status = policy.status_payload()
    assert status["page_mode"] == "rich-page-audit-quality-first-v4"
    assert status["page_image_detail"] == "high"
    assert status["page_reasoning_effort"] == "low"
    assert status["selective_variant_crops"] is True
    assert status["variant_crop_confidence_threshold"] == 0.80
    assert status["variant_crop_max_monthly_dkk"] == 5.0
    assert status["visual_only_member_price_requires_crop"] is True
    assert status["current_luna_input_usd_per_million"] == 0.20
    assert status["current_luna_output_usd_per_million"] == 1.20


def test_global_monthly_guard_remains_20_dkk():
    assert luna.DEFAULT_CONFIG["monthly_budget_dkk"] == 20.0

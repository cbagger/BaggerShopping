import json

from app import luna_enrichment as luna
from app import luna_semantic_audit as semantic
from app import luna_semantic_guards as guards
from app.meny_flyer import Offer, Publication


def _isolated(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    store = tmp_path / "store.json"
    config.write_text(
        json.dumps({"enabled": True, "apply_results": True, "min_apply_confidence": 0.96}),
        encoding="utf-8",
    )
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)


def _offer():
    return Offer(
        id="cheasy",
        retailer="365discount",
        publication_id="pub-365",
        publication_title="Uge 34",
        product_name="Cheasy yoghurt",
        price=12,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text="Cheasy yoghurt 1 kg 12 kr",
    )


def _facts(package_size="1 kg; 1 stk."):
    return {
        "visible": True,
        "same_offer": True,
        "product_name": "Cheasy yoghurt",
        "brand": "Cheasy",
        "ordinary_price": 12,
        "member_price": None,
        "member_program": None,
        "member_app": None,
        "membership_price_visible": False,
        "requires_activation": False,
        "before_price": None,
        "unit_price": "12,00 kr/kg",
        "package_size": package_size,
        "multiple_products": True,
        "variants": ["Vanilje", "Skovbær", "Mango & banan"],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.99,
        "needs_crop_verification": False,
    }


def test_exact_one_kg_price_can_equal_per_kg_price():
    offer = _offer()
    facts = _facts()

    assert "page-audit-ordinary-price-is-unit-price" not in guards._pricing_sanity_reasons(
        offer, facts
    )
    assert guards.mandatory_pricing_crop_resolved(
        offer, facts, {"min_apply_confidence": 0.96}
    )


def test_same_numeric_unit_price_stays_suspicious_for_non_equivalent_package():
    offer = _offer()
    facts = _facts(package_size="500 g; 1 bæger")

    assert "page-audit-ordinary-price-is-unit-price" in guards._pricing_sanity_reasons(
        offer, facts
    )
    assert not guards.mandatory_pricing_crop_resolved(
        offer, facts, {"min_apply_confidence": 0.96}
    )


def test_failed_completed_crop_is_reused_after_guard_fix(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    publication = Publication(
        id="pub-365",
        retailer="365discount",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://images.test/page.jpg"],
        structured_offers=[offer],
    )
    fingerprint = luna.offer_fingerprint(offer)
    facts = _facts()

    luna.save_store(
        {
            "records": {
                fingerprint: {
                    "status": "failed",
                    "analysis_level": "crop",
                    "error": "completed",
                    "semantic_facts": facts,
                }
            },
            "pricing_index": {},
            "usage": {},
            "events": [],
            "semantic_facts": {
                semantic.offer_key(offer): {
                    "source": "page-audit",
                    "page_fingerprint": "page",
                    "needs_crop": True,
                    "crop_reasons": ["page-audit-ordinary-price-is-unit-price"],
                    "facts": facts,
                }
            },
        }
    )

    assert guards._crop_candidates_allowing_build58_reverification([publication]) == []


def test_exact_one_liter_member_price_can_equal_per_liter_price():
    offer = _offer()
    facts = _facts(package_size="1 liter")
    facts.update(
        {
            "ordinary_price": 14,
            "member_price": 12,
            "member_program": "Plus",
            "membership_price_visible": True,
            "unit_price": "12,00 kr/l",
        }
    )

    assert "page-audit-member-price-is-unit-price" not in guards._pricing_sanity_reasons(
        offer, facts
    )
    assert guards.mandatory_pricing_crop_resolved(
        offer, facts, {"min_apply_confidence": 0.96}
    )

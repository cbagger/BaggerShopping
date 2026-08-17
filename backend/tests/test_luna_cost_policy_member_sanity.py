from app import luna_cost_policy as policy
from app import luna_semantic_guards as guards
from app.meny_flyer import Offer


def _offer(*, retailer="Bilka", price=85.0, name="Neophos maskinopvask", raw_text="Bilka Plus"):
    return Offer(
        id="offer",
        retailer=retailer,
        publication_id="pub",
        publication_title="Uge 34",
        product_name=name,
        price=price,
        normal_price=None,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text=raw_text,
        quality_score=0.99,
    )


def _facts(**updates):
    value = {
        "visible": True,
        "ordinary_price": 85,
        "member_price": 79,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus",
        "membership_price_visible": True,
        "unit_price": "Pr. stk. max. 1,98 (plus); Pr. stk. max. 2,13",
        "pricing_confidence": 0.99,
        "multiple_products": False,
        "variants": [],
        "variant_confidence": 0.99,
        "needs_crop_verification": False,
    }
    value.update(updates)
    return value


def test_worker_gate_preserves_neophos_unit_price_sanity(monkeypatch):
    offer = _offer()
    facts = _facts(member_price=1.98)

    generic = guards._pricing_sanity_reasons(offer, facts)
    assert "page-audit-member-price-is-unit-price" in generic
    assert policy._pricing_crop_needed(offer, facts, 0.96) is True
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True

    monkeypatch.setattr(
        policy,
        "load_config",
        lambda: {
            "min_apply_confidence": 0.96,
            "selective_variant_crops": False,
        },
    )
    reasons = policy._balanced_crop_reasons(offer, facts, True)
    assert "page-audit-member-price-is-unit-price" in reasons
    assert policy.is_variant_only_crop(reasons) is False


def test_worker_gate_crops_visible_spir_plus_price_even_when_amount_was_missed(monkeypatch):
    offer = _offer(
        retailer="Netto",
        price=12,
        name="SPIR plantedrik",
        raw_text="SPIR plantedrik 1 liter",
    )
    facts = _facts(
        ordinary_price=12,
        member_price=None,
        member_program=None,
        member_app=None,
        membership_price_visible=True,
        unit_price=None,
    )

    generic = guards._pricing_sanity_reasons(offer, facts)
    assert "page-audit-visible-member-price-missing-value" in generic
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is True

    monkeypatch.setattr(
        policy,
        "load_config",
        lambda: {
            "min_apply_confidence": 0.96,
            "selective_variant_crops": False,
        },
    )
    reasons = policy._balanced_crop_reasons(offer, facts, True)
    assert "page-audit-visible-member-price-missing-value" in reasons


def test_worker_gate_keeps_resolved_neophos_85_79_without_extra_crop():
    offer = _offer(raw_text="Neophos maskinopvask Bilka Plus pris 79 kr")
    facts = _facts()

    assert guards._pricing_sanity_reasons(offer, facts) == ()
    assert policy._balanced_server_needs_crop(offer, facts, 0.96) is False

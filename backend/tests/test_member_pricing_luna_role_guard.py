from app import member_pricing_v3 as v3
from app import member_pricing_v4 as v4
from app import luna_semantic_audit as semantic
from app import luna_semantic_guards as guards
from app.member_pricing import detect_member_pricing as detect_public_member_pricing
from app.luna_enrichment import offer_fingerprint
from app.meny_flyer import Offer, Publication


def _offer(*, price=29.0):
    return Offer(
        id="seafood-offer-19",
        retailer="føtex",
        publication_id="foetex-current",
        publication_title="Uge 34/35",
        product_name="Salling Seafoodmix, vannameirejer, tunsteak eller -poke",
        price=price,
        normal_price=None,
        source_url="https://example.test/flyer",
        page_number=19,
        image_url="https://example.test/seafood-crop.webp",
        hotspot_x=0.48,
        hotspot_y=0.02,
        hotspot_width=0.25,
        hotspot_height=0.29,
        raw_text="Salling Seafoodmix, vannameirejer, tunsteak eller -poke 150-300 g",
        hotspot_confidence=0.99,
        quality_score=0.99,
        quality_source="tjek-catalog",
    )


def _publication(offer):
    return Publication(
        id=offer.publication_id,
        retailer=offer.retailer,
        title=offer.publication_title,
        valid_from="13.08.2026",
        valid_until="27.08.2026",
        status="current",
        source_url=offer.source_url,
        page_count=19,
        page_image_urls=[f"https://example.test/page-{number}.webp" for number in range(1, 20)],
        structured_offers=[offer],
    )


def test_unresolved_luna_primary_member_role_is_fail_closed(monkeypatch):
    """Live regression: provider 29 + Luna member 29/ordinary null is not safe."""

    def ambiguous_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": None,
            "member_price": 29,
            "member_program": "føtex plus",
            "member_app": "føtex plus appen",
            "requires_activation": False,
            "pricing_confidence": 0.98,
        }

    monkeypatch.setattr(v3, "_luna_override", ambiguous_luna)

    result = v4.detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=None,
        text="Salling Seafoodmix 150-300 g",
    )

    assert result is None


def test_public_fallback_cannot_promote_unit_price_after_luna_is_rejected(monkeypatch):
    """Exact live follow-on: 166.67 kr/kg must never replace provider price 29."""

    def ambiguous_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": None,
            "member_price": 29,
            "member_program": "føtex plus",
            "member_app": "føtex plus appen",
            "requires_activation": False,
            "pricing_confidence": 0.98,
        }

    monkeypatch.setattr(v3, "_luna_override", ambiguous_luna)

    result = detect_public_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=None,
        text=(
            "Salling Seafoodmix, vannameirejer, tunsteak eller -poke | "
            "plus pris 166,67 kr/kg max. | føtex Plus"
        ),
        unit_price="166,67 kr/kg max. (plus); 193,33 kr/kg max.",
    )

    assert result is None


def test_public_fallback_rejects_any_member_candidate_above_provider_product_price(monkeypatch):
    monkeypatch.setattr(v3, "_luna_override", lambda **_: None)

    result = detect_public_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=None,
        text="Salling Seafoodmix | plus pris 166,67",
    )

    assert result is None


def test_public_fallback_still_accepts_real_member_price_below_provider_price(monkeypatch):
    monkeypatch.setattr(v3, "_luna_override", lambda **_: None)

    result = detect_public_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=None,
        text="Salling Seafoodmix | plus pris 25 kr | føtex Plus",
    )

    assert result is not None
    assert result.ordinary_price == 29
    assert result.member_price == 25


def test_resolved_luna_member_role_remains_customer_visible(monkeypatch):
    def verified_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": 29,
            "member_price": 25,
            "member_program": "føtex Plus",
            "member_app": "føtex Plus",
            "requires_activation": False,
            "pricing_confidence": 0.99,
        }

    monkeypatch.setattr(v3, "_luna_override", verified_luna)

    result = v4.detect_member_pricing(
        retailer="føtex",
        price=29,
        normal_price=None,
        text="Salling Seafoodmix 150-300 g",
    )

    assert result is not None
    assert result.ordinary_price == 29
    assert result.member_price == 25
    assert result.source == "luna-verified"


def test_page_audit_primary_member_role_is_ambiguous():
    offer = _offer()
    facts = {
        "visible": True,
        "ordinary_price": None,
        "member_price": 29,
        "pricing_confidence": 0.98,
    }

    assert guards._primary_member_role_ambiguous(offer, facts) is True
    assert guards._strict_server_needs_crop(offer, facts, 0.96) is True


def test_old_needs_crop_false_page_audit_is_reopened_for_targeted_crop(monkeypatch):
    offer = _offer()
    publication = _publication(offer)
    facts = {
        "visible": True,
        "ordinary_price": None,
        "member_price": 29,
        "member_program": "føtex plus",
        "member_app": "føtex plus appen",
        "pricing_confidence": 0.98,
    }
    fingerprint = offer_fingerprint(offer)
    store = {
        "semantic_facts": {
            semantic.offer_key(offer): {
                "source": "page-audit",
                "page_fingerprint": "page-19-v1",
                "facts": facts,
                "needs_crop": False,
                "crop_reasons": [],
            }
        },
        "records": {
            fingerprint: {
                "status": "completed",
                "analysis_level": "page-audit",
                "facts": {"same_offer": True, **facts},
            }
        },
    }

    monkeypatch.setattr(guards, "load_store", lambda: store)

    candidates = guards._crop_candidates_allowing_build58_reverification([publication])

    assert len(candidates) == 1
    assert candidates[0].offer.id == offer.id
    assert "page-audit-primary-price-role-ambiguous" in candidates[0].reasons


def test_resolved_29_25_page_audit_is_not_reopened(monkeypatch):
    offer = _offer()
    publication = _publication(offer)
    facts = {
        "visible": True,
        "ordinary_price": 29,
        "member_price": 25,
        "member_program": "føtex Plus",
        "pricing_confidence": 0.99,
    }
    store = {
        "semantic_facts": {
            semantic.offer_key(offer): {
                "source": "page-audit",
                "page_fingerprint": "page-19-v2",
                "facts": facts,
                "needs_crop": False,
                "crop_reasons": [],
            }
        },
        "records": {},
    }

    monkeypatch.setattr(guards, "load_store", lambda: store)

    candidates = guards._crop_candidates_allowing_build58_reverification([publication])
    assert candidates == []


def test_spir_visible_member_badge_without_amount_requires_crop():
    offer = _offer(price=12).model_copy(
        update={
            "id": "spir-10",
            "retailer": "Netto",
            "product_name": "SPIR plantedrik",
            "raw_text": "SPIR plantedrik 1 liter",
        }
    )
    facts = {
        "visible": True,
        "ordinary_price": 12,
        "member_price": None,
        "member_program": "Netto+",
        "member_app": "Netto+ appen",
        "membership_price_visible": True,
        "unit_price": None,
        "pricing_confidence": 0.99,
    }

    reasons = guards._pricing_sanity_reasons(offer, facts)
    assert "page-audit-visible-member-price-missing-value" in reasons
    assert guards._strict_server_needs_crop(offer, facts, 0.96) is True


def test_spir_resolved_12_9_does_not_trigger_generic_sanity_crop():
    offer = _offer(price=12).model_copy(
        update={
            "id": "spir-10",
            "retailer": "Netto",
            "product_name": "SPIR plantedrik",
        }
    )
    facts = {
        "visible": True,
        "ordinary_price": 12,
        "member_price": 9,
        "member_program": "Netto+",
        "member_app": "Netto+ appen",
        "membership_price_visible": True,
        "unit_price": None,
        "pricing_confidence": 0.99,
    }

    assert guards._pricing_sanity_reasons(offer, facts) == ()


def test_neophos_unit_price_cannot_be_member_price_even_with_high_confidence():
    offer = _offer(price=85).model_copy(
        update={
            "id": "neophos-40",
            "retailer": "Bilka",
            "product_name": "Neophos maskinopvask",
            "unit_price": "Pr. stk. max. 2,13",
        }
    )
    facts = {
        "visible": True,
        "ordinary_price": 85,
        "member_price": 1.98,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus appen",
        "membership_price_visible": True,
        "unit_price": "Pr. stk. max. 1,98 (plus); Pr. stk. max. 2,13",
        "pricing_confidence": 0.99,
    }

    reasons = guards._pricing_sanity_reasons(offer, facts)
    assert "page-audit-member-price-is-unit-price" in reasons
    assert "page-audit-extreme-member-discount-needs-verification" in reasons
    assert guards._strict_server_needs_crop(offer, facts, 0.96) is True


def test_neophos_correct_85_79_is_safe_even_with_unit_price_metadata():
    offer = _offer(price=85).model_copy(
        update={
            "id": "neophos-40",
            "retailer": "Bilka",
            "product_name": "Neophos maskinopvask",
            "unit_price": "Pr. stk. max. 2,13",
        }
    )
    facts = {
        "visible": True,
        "ordinary_price": 85,
        "member_price": 79,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus appen",
        "membership_price_visible": True,
        "unit_price": "Pr. stk. max. 1,98 (plus); Pr. stk. max. 2,13",
        "pricing_confidence": 0.99,
    }

    assert guards._pricing_sanity_reasons(offer, facts) == ()


def test_cached_luna_neophos_unit_price_is_fail_closed_until_crop(monkeypatch):
    def wrong_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": 85,
            "member_price": 1.98,
            "member_program": "Bilka Plus",
            "member_app": "Bilka Plus",
            "requires_activation": False,
            "pricing_confidence": 0.99,
        }

    monkeypatch.setattr(v3, "_luna_override", wrong_luna)

    result = v4.detect_member_pricing(
        retailer="Bilka",
        price=85,
        normal_price=None,
        text="Neophos maskinopvask | Pr. stk. max. 1,98 | Pr. stk. max. 2,13",
        unit_price="Pr. stk. max. 2,13",
    )

    assert result is None


def test_correct_luna_neophos_85_79_remains_visible(monkeypatch):
    def correct_luna(**_):
        return {
            "authoritative": True,
            "ordinary_price": 85,
            "member_price": 79,
            "member_program": "Bilka Plus",
            "member_app": "Bilka Plus",
            "requires_activation": False,
            "pricing_confidence": 0.99,
        }

    monkeypatch.setattr(v3, "_luna_override", correct_luna)

    result = v4.detect_member_pricing(
        retailer="Bilka",
        price=85,
        normal_price=None,
        text="Neophos maskinopvask | Pr. stk. max. 1,98 | Pr. stk. max. 2,13",
        unit_price="Pr. stk. max. 2,13",
    )

    assert result is not None
    assert result.ordinary_price == 85
    assert result.member_price == 79


def test_new_semantic_contract_changes_page_fingerprint():
    offer = _offer()
    publication = _publication(offer)
    original = guards._original_page_fingerprint(publication, 19, [offer])
    versioned = guards._versioned_page_fingerprint(publication, 19, [offer])

    assert original != versioned
    assert len(versioned) == 32

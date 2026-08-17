from __future__ import annotations

import json

from app import luna_cost_policy as policy
from app import luna_enrichment as luna
from app import luna_semantic_audit as semantic
from app.meny_flyer import Offer, OfferVariant, Publication


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


def _offer(offer_id="becel", *, price=15, variants=None, confidence=0.62):
    return Offer(
        id=offer_id,
        retailer="Bilka",
        publication_id="pub",
        publication_title="Uge 34",
        product_name="Becel flydende" if offer_id == "becel" else "Testkampagne",
        price=price,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        raw_text="provider text",
        variants=variants or [],
        variant_confidence=confidence,
        quality_score=0.97,
    )


def _candidate(*offers):
    publication = Publication(
        id="pub",
        retailer="Bilka",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://images.test/page.jpg?rotating=1"],
        structured_offers=list(offers),
    )
    return semantic.PageAuditCandidate(
        fingerprint="page",
        publication=publication,
        page_number=1,
        image_url=publication.page_image_urls[0],
        offers=tuple(offers),
    )


def _row(offer_id, **updates):
    value = {
        "id": offer_id,
        "vis": True,
        "o": 15,
        "m": None,
        "p": None,
        "a": False,
        "x": False,
        "v": [],
        "pc": 0.99,
        "vc": 0.8,
        "r": "none",
    }
    value.update(updates)
    return value


def test_page_scout_is_low_detail_and_compact_by_default(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    candidate = _candidate(_offer())
    body = policy._cost_page_request_body(candidate, luna.load_config())
    image = body["input"][0]["content"][1]
    assert image["detail"] == "low"
    assert body["max_output_tokens"] == 1400
    schema = body["text"]["format"]["schema"]
    keys = set(schema["properties"]["offers"]["items"]["properties"])
    assert keys == {"id", "vis", "o", "m", "p", "a", "x", "v", "pc", "vc", "r"}


def test_compact_output_requires_exactly_every_hotspot():
    allowed = {"one", "two"}
    assert policy._cost_validate_page_output(
        {"offers": [_row("one")]}, allowed
    ) is None
    rows = policy._cost_validate_page_output(
        {"offers": [_row("one"), _row("two")]}, allowed
    )
    assert rows is not None
    assert {row["offer_id"] for row in rows} == allowed


def test_becel_style_high_confidence_member_price_needs_no_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = policy._expanded_row(
        _row(
            "becel",
            o=15,
            m=12,
            p="Bilka Plus",
            pc=0.99,
            r="none",
        ),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is False
    assert facts["ordinary_price"] == 15
    assert facts["member_price"] == 12


def test_variant_only_uncertainty_does_not_spend_proactive_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path, proactive_variant_crops=False)
    offer = _offer("castello")
    facts = policy._expanded_row(
        _row(
            "castello",
            x=True,
            v=["Saga", "Creamy White", "Creamy Blue"],
            vc=0.93,
            r="variant",
        ),
        {"castello"},
    )
    assert facts is not None
    assert facts["multiple_products"] is True
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is False
    assert policy._cost_crop_reasons(offer, facts, False) == []


def test_missing_member_amount_still_gets_proactive_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = policy._expanded_row(
        _row("becel", o=15, m=None, p="Bilka Plus", pc=0.72, r="member"),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is True
    reasons = policy._cost_crop_reasons(offer, facts, True)
    assert "page-scout-member-uncertain" in reasons
    assert "page-scout-member-price-missing" in reasons


def test_price_conflict_still_gets_proactive_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(price=15)
    facts = policy._expanded_row(
        _row("becel", o=20, m=12, p="Bilka Plus", pc=0.99),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is True
    assert "page-scout-provider-price-conflict" in policy._cost_crop_reasons(offer, facts, True)


def test_weight_like_variant_is_filtered():
    expanded = policy._expanded_row(
        _row("one", x=True, v=["500 g", "Original", "Flere varianter"], vc=0.99),
        {"one"},
    )
    assert expanded is not None
    assert expanded["variants"] == ["Original"]


def test_default_config_has_hard_monthly_cost_guard():
    assert luna.DEFAULT_CONFIG["monthly_budget_dkk"] == 20.0
    assert luna.DEFAULT_CONFIG["proactive_variant_crops"] is False
    assert luna.DEFAULT_CONFIG["page_scout_image_detail"] == "low"

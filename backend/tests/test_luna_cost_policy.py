from __future__ import annotations

import json

from app import luna_cost_policy as policy
from app import luna_enrichment as luna
from app import luna_semantic_audit as semantic
from app.meny_flyer import Offer, Publication


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


def _offer(offer_id="becel", *, price=15):
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
        variant_confidence=0.62,
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


def _short(offer_id: str, allowed: set[str]) -> str:
    return policy._short_id_map(allowed)[offer_id]


def _row(offer_id, allowed=None, **updates):
    allowed = allowed or {offer_id}
    value = {
        "i": _short(offer_id, allowed),
        "o": 15,
        "m": None,
        "p": None,
        "a": False,
        "x": False,
        "c": 0.99,
        "q": False,
    }
    value.update(updates)
    return value


def test_page_scout_is_ultracompact_low_detail_and_minimal_reasoning(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path, page_scout_max_output_tokens=1400)
    candidate = _candidate(_offer())
    body = policy._cost_page_request_body(candidate, luna.load_config())
    image = body["input"][0]["content"][1]
    assert image["detail"] == "low"
    assert body["reasoning"]["effort"] == "minimal"
    assert body["max_output_tokens"] <= 700
    schema = body["text"]["format"]["schema"]
    keys = set(schema["properties"]["r"]["items"]["properties"])
    assert keys == {"i", "o", "m", "p", "a", "x", "c", "q"}


def test_short_ids_are_unique_and_smaller_than_provider_ids():
    ids = {"uw-ZUPfzrphh568o3kzTd-9", "ux-12345678901234567890", "ab-999999999999"}
    mapping = policy._short_id_map(ids)
    assert len(set(mapping.values())) == len(ids)
    assert all(len(mapping[value]) < len(value) for value in ids)


def test_compact_output_requires_exactly_every_hotspot():
    allowed = {"one-long-id", "two-long-id"}
    assert policy._cost_validate_page_output(
        {"r": [_row("one-long-id", allowed)]}, allowed
    ) is None
    rows = policy._cost_validate_page_output(
        {"r": [_row("one-long-id", allowed), _row("two-long-id", allowed)]}, allowed
    )
    assert rows is not None
    assert {row["offer_id"] for row in rows} == allowed


def test_live_becel_shape_high_confidence_member_price_needs_no_crop_even_if_q(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = policy._expanded_row(
        _row("becel", o=15, m=12, p="Bilka Plus", c=0.99, q=True),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is False
    assert policy._cost_crop_reasons(offer, facts, False) == []
    assert facts["ordinary_price"] == 15
    assert facts["member_price"] == 12


def test_multi_product_flag_survives_without_variant_names(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer("castello")
    facts = policy._expanded_row(
        _row("castello", x=True, c=0.98),
        {"castello"},
    )
    assert facts is not None
    assert facts["multiple_products"] is True
    assert facts["variants"] == []
    assert facts["variant_confidence"] == 0.0
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is False


def test_missing_member_amount_still_gets_proactive_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = policy._expanded_row(
        _row("becel", o=15, m=None, p="Bilka Plus", c=0.72, q=True),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is True
    reasons = policy._cost_crop_reasons(offer, facts, True)
    assert "page-scout-member-price-missing" in reasons
    assert "page-scout-price-association-uncertain" in reasons


def test_price_conflict_still_gets_proactive_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer(price=15)
    facts = policy._expanded_row(
        _row("becel", o=20, m=12, p="Bilka Plus", c=0.99),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is True
    assert "page-scout-provider-price-conflict" in policy._cost_crop_reasons(offer, facts, True)


def test_invalid_member_relation_still_gets_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    facts = policy._expanded_row(
        _row("becel", o=12, m=15, p="Bilka Plus", c=0.99),
        {"becel"},
    )
    assert facts is not None
    assert policy._cost_server_needs_crop(offer, facts, 0.96) is True


def test_page_scout_does_not_request_variant_names_in_prompt(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    prompt = policy._page_scout_prompt(_candidate(_offer()))
    assert "Do not return product names, brands, weights or variant names" in prompt
    schema = policy._compact_schema(_candidate(_offer()))
    props = schema["properties"]["r"]["items"]["properties"]
    assert "v" not in props
    assert "vc" not in props


def test_default_config_keeps_monthly_guard():
    assert luna.DEFAULT_CONFIG["monthly_budget_dkk"] == 20.0
    assert luna.DEFAULT_CONFIG["proactive_variant_crops"] is False
    assert luna.DEFAULT_CONFIG["page_scout_image_detail"] == "low"

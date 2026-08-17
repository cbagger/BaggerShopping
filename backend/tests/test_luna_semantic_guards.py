import json

from app import luna_enrichment as luna
from app import luna_semantic_audit as semantic
from app import luna_semantic_guards as guards
from app.meny_flyer import Offer, Publication


def _isolated(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    store = tmp_path / "store.json"
    config.write_text(json.dumps({"enabled": True, "apply_results": True}), encoding="utf-8")
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)


def _offer(offer_id="one"):
    return Offer(
        id=offer_id,
        retailer="Bilka",
        publication_id="pub",
        publication_title="Uge 34",
        product_name=f"Offer {offer_id}",
        price=15,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text="test",
    )


def _valid_row(offer_id):
    return {
        "offer_id": offer_id,
        "visible": True,
        "product_name": "Test",
        "brand": None,
        "ordinary_price": 15,
        "member_price": None,
        "member_program": None,
        "member_app": None,
        "requires_activation": False,
        "before_price": None,
        "unit_price": None,
        "package_size": None,
        "multiple_products": False,
        "variants": [],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.80,
        "needs_crop_verification": False,
    }


def test_partial_page_output_is_rejected():
    rows = {"offers": [_valid_row("one")]}
    assert guards._strict_validate_page_output(rows, {"one", "two"}) is None
    accepted = guards._strict_validate_page_output(
        {"offers": [_valid_row("one"), _valid_row("two")]},
        {"one", "two"},
    )
    assert accepted is not None
    assert len(accepted) == 2


def test_safe_build58_page_result_upgrades_legacy_v1_record(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    fingerprint = luna.offer_fingerprint(offer)
    legacy_signature = luna.offer_pricing_signature(offer)
    store = {
        "records": {
            fingerprint: {
                "status": "completed",
                # No analysis_level means a legacy Build56/57 Luna result.
                "facts": {
                    "same_offer": True,
                    "ordinary_price": 15,
                    "member_price": None,
                    "pricing_confidence": 0.99,
                },
            }
        },
        "pricing_index": {legacy_signature: fingerprint},
        "usage": {},
        "events": [],
    }
    facts = {
        "visible": True,
        "product_name": "Offer one",
        "brand": None,
        "ordinary_price": 15,
        "member_price": 12,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus",
        "requires_activation": False,
        "before_price": None,
        "unit_price": None,
        "package_size": None,
        "multiple_products": False,
        "variants": [],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.80,
        "needs_crop_verification": False,
    }
    guards._index_page_pricing_upgrading_legacy(
        store,
        offer,
        facts,
        needs_crop=False,
        page_fingerprint_value="page-new",
    )
    upgraded = store["records"][fingerprint]
    assert upgraded["analysis_level"] == "page-audit"
    assert upgraded["facts"]["member_price"] == 12
    assert upgraded["page_fingerprint"] == "page-new"


def test_old_v1_record_does_not_block_build58_requested_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    publication = Publication(
        id="pub",
        retailer="Bilka",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://images.test/page.jpg"],
        structured_offers=[offer],
    )
    fingerprint = luna.offer_fingerprint(offer)
    luna.save_store({
        "records": {
            fingerprint: {
                "status": "completed",
                # No analysis_level means a legacy Build56/57 Luna result.
                "facts": {"same_offer": True, "pricing_confidence": 0.99},
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
                "crop_reasons": ["page-audit-variant-uncertain"],
                "facts": {"visible": True},
            }
        },
    })
    candidates = guards._crop_candidates_allowing_build58_reverification([publication])
    assert len(candidates) == 1
    assert candidates[0].fingerprint == fingerprint


def test_completed_build58_crop_satisfies_crop_request(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    offer = _offer()
    publication = Publication(
        id="pub",
        retailer="Bilka",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://images.test/page.jpg"],
        structured_offers=[offer],
    )
    fingerprint = luna.offer_fingerprint(offer)
    luna.save_store({
        "records": {
            fingerprint: {"status": "completed", "analysis_level": "crop"}
        },
        "pricing_index": {},
        "usage": {},
        "events": [],
        "semantic_facts": {
            semantic.offer_key(offer): {
                "source": "page-audit",
                "page_fingerprint": "page",
                "needs_crop": True,
                "facts": {"visible": True},
            }
        },
    })
    assert guards._crop_candidates_allowing_build58_reverification([publication]) == []

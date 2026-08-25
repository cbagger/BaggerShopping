import json

from app import luna_enrichment as luna
from app.luna_overlay import apply_cached_enrichment
from app.luna_semantic_audit import offer_key
from app.meny_flyer import Offer, Publication


def _configure(monkeypatch, tmp_path, *, enabled=True):
    config = tmp_path / "luna-config.json"
    store = tmp_path / "luna-store.json"
    config.write_text(json.dumps({
        "enabled": enabled,
        "apply_results": True,
        "min_apply_confidence": 0.96,
    }), encoding="utf-8")
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)


def _publication():
    publication = Publication(
        id="week", retailer="MENY", title="Uge", source_url="https://example.test",
        page_count=1,
    )
    publication.structured_offers = [Offer(
        id="offer", retailer="MENY", publication_id="week", publication_title="Uge",
        product_name="Kaffe eller espresso", price=50, source_url="https://example.test",
        page_number=1, raw_text="Kaffe eller espresso", quality_score=0.9,
        variant_confidence=0.4,
    )]
    return publication


def test_cached_luna_variants_can_fill_only_a_weak_variant_result(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=True)
    publication = _publication()
    offer = publication.structured_offers[0]
    fingerprint = luna.offer_fingerprint(offer)
    luna.save_store({
        "records": {fingerprint: {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "brand": "Merrild",
                "identity_confidence": 0.99,
                "variants": ["Merrild Crema", "Merrild Espresso"],
                "variant_confidence": 0.995,
            },
        }},
        "pricing_index": {}, "usage": {}, "events": [],
    })

    enriched = apply_cached_enrichment([publication])[0].structured_offers[0]
    assert enriched.product_name == offer.product_name
    assert enriched.price == offer.price
    assert enriched.hotspot_x == offer.hotspot_x
    assert enriched.brand == "Merrild"
    assert [variant.name for variant in enriched.variants] == ["Merrild Crema", "Merrild Espresso"]
    assert "luna-verified-variants" in enriched.quality_signals


def test_semantic_multi_product_variants_surface_in_picker_at_medium_confidence(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=True)
    publication = _publication()
    offer = publication.structured_offers[0]
    luna.save_store({
        "records": {},
        "semantic_facts": {
            offer_key(offer): {
                "source": "page-audit",
                "needs_crop": False,
                "facts": {
                    "visible": True,
                    "same_offer": True,
                    "multiple_products": True,
                    "variants": ["Merrild Crema", "Merrild Espresso"],
                    "variant_confidence": 0.85,
                    "identity_confidence": 0.85,
                    "pricing_confidence": 0.0,
                },
            }
        },
        "pricing_index": {}, "usage": {}, "events": [],
    })

    enriched = apply_cached_enrichment([publication])[0].structured_offers[0]

    assert [variant.name for variant in enriched.variants] == [
        "Merrild Crema", "Merrild Espresso"
    ]
    assert enriched.variant_confidence == 0.85
    assert "luna-picker-variants" in enriched.quality_signals
    assert "luna-multiple-products" in enriched.quality_signals
    assert "luna-verified-variants" not in enriched.quality_signals


def test_medium_confidence_luna_variants_require_explicit_multi_product_evidence(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=True)
    publication = _publication()
    offer = publication.structured_offers[0]
    luna.save_store({
        "records": {},
        "semantic_facts": {
            offer_key(offer): {
                "source": "page-audit",
                "needs_crop": False,
                "facts": {
                    "visible": True,
                    "same_offer": True,
                    "multiple_products": False,
                    "variants": ["Merrild Crema", "Merrild Espresso"],
                    "variant_confidence": 0.95,
                    "identity_confidence": 0.95,
                    "pricing_confidence": 0.0,
                },
            }
        },
        "pricing_index": {}, "usage": {}, "events": [],
    })

    enriched = apply_cached_enrichment([publication])[0].structured_offers[0]

    assert enriched.variants == []
    assert "luna-picker-variants" not in enriched.quality_signals


def test_luna_off_returns_deterministic_offer_unchanged(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=False)
    publication = _publication()
    offer = publication.structured_offers[0]
    fingerprint = luna.offer_fingerprint(offer)
    luna.save_store({
        "records": {fingerprint: {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "brand": "Should not apply",
                "identity_confidence": 1.0,
                "variants": ["Should not apply"],
                "variant_confidence": 1.0,
            },
        }},
        "pricing_index": {}, "usage": {}, "events": [],
    })

    result = apply_cached_enrichment([publication])
    assert result[0] is publication
    assert result[0].structured_offers[0] is offer
    assert offer.brand is None
    assert offer.variants == []


def test_strong_deterministic_variants_are_never_replaced(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=True)
    publication = _publication()
    offer = publication.structured_offers[0].model_copy(update={
        "variant_confidence": 0.95,
        "variants": [],
    })
    publication.structured_offers = [offer]
    fingerprint = luna.offer_fingerprint(offer)
    luna.save_store({
        "records": {fingerprint: {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "brand": None,
                "identity_confidence": 1.0,
                "variants": ["AI variant"],
                "variant_confidence": 1.0,
            },
        }},
        "pricing_index": {}, "usage": {}, "events": [],
    })

    enriched = apply_cached_enrichment([publication])[0].structured_offers[0]
    assert enriched.variants == []
    assert enriched.variant_confidence == 0.95


def test_luna_future_validity_preserves_existing_add_safety(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enabled=True)
    publication = _publication()
    original = publication.structured_offers[0]
    offer = original.model_copy(update={"safe_to_add": True})
    publication.structured_offers = [offer]

    luna.save_store({
        "records": {},
        "semantic_facts": {
            offer_key(offer): {
                "source": "page-audit",
                "needs_crop": False,
                "facts": {
                    "visible": True,
                    "same_offer": True,
                    "multiple_products": False,
                    "variants": [],
                    "variant_confidence": 0.0,
                    "identity_confidence": 0.0,
                    "pricing_confidence": 0.0,
                    "offer_valid_from": "31.12.2099",
                    "offer_valid_until": "02.01.2100",
                    "validity_confidence": 1.0,
                },
            }
        },
        "pricing_index": {}, "usage": {}, "events": [],
    })

    enriched = apply_cached_enrichment([publication])[0].structured_offers[0]
    assert enriched.valid_from == "31.12.2099"
    assert enriched.valid_until == "02.01.2100"
    assert enriched.safe_to_add is True
    assert "luna-offer-validity" in enriched.quality_signals
    assert "luna-future-offer" not in enriched.quality_signals

    publication.structured_offers = [offer.model_copy(update={"safe_to_add": False})]
    unsafe_offer = publication.structured_offers[0]
    luna.save_store({
        "records": {},
        "semantic_facts": {
            offer_key(unsafe_offer): {
                "source": "page-audit",
                "needs_crop": False,
                "facts": {
                    "visible": True,
                    "same_offer": True,
                    "multiple_products": False,
                    "variants": [],
                    "variant_confidence": 0.0,
                    "identity_confidence": 0.0,
                    "pricing_confidence": 0.0,
                    "offer_valid_from": "31.12.2099",
                    "offer_valid_until": "02.01.2100",
                    "validity_confidence": 1.0,
                },
            }
        },
        "pricing_index": {}, "usage": {}, "events": [],
    })

    still_unsafe = apply_cached_enrichment([publication])[0].structured_offers[0]
    assert still_unsafe.safe_to_add is False

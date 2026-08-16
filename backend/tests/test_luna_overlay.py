import json

from app import luna_enrichment as luna
from app.luna_overlay import apply_cached_enrichment
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

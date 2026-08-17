import json

from app import luna_overlay
from app.meny_flyer import Offer, Publication


def _configure(monkeypatch, tmp_path):
    readiness = tmp_path / "flyer-readiness.json"
    readiness.write_text(json.dumps({
        "version": 1,
        "initialized": True,
        "publications": {},
        "updated_at": 1,
    }), encoding="utf-8")
    monkeypatch.setenv("FLYER_READINESS_STORE_PATH", str(readiness))
    monkeypatch.setenv("FLYER_SERVING_CACHE_PATH", str(tmp_path / "flyer-serving-cache.json"))
    monkeypatch.setattr(
        luna_overlay,
        "load_config",
        lambda: {"enabled": False, "apply_results": True},
    )


def _publication(*, title="Uge 34", price=15.0, with_offer=True):
    publication = Publication(
        id="rema-week",
        retailer="REMA 1000",
        title=title,
        valid_from="17.08.2026",
        valid_until="23.08.2026",
        status="current",
        source_url="https://example.test/rema",
        page_count=1,
        page_image_urls=["https://example.test/rema/page-1.jpg"],
    )
    if with_offer:
        publication.structured_offers = [Offer(
            id="offer-1",
            retailer="REMA 1000",
            publication_id=publication.id,
            publication_title=publication.title,
            product_name="Kohberg brød",
            price=price,
            source_url="https://example.test/rema",
            page_number=1,
            hotspot_x=0.1,
            hotspot_y=0.2,
            hotspot_width=0.3,
            hotspot_height=0.2,
            hotspot_confidence=0.95,
            raw_text="Kohberg brød 15 kr",
        )]
    return publication


def test_processing_replacement_keeps_last_served_hotspots(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: False)

    first = _publication(title="Uge 34", price=15.0, with_offer=True)
    served = luna_overlay.apply_cached_enrichment([first])
    assert len(served) == 1
    assert len(served[0].structured_offers) == 1
    assert served[0].structured_offers[0].hotspot_x == 0.1

    replacement = _publication(title="Uge 35", price=12.0, with_offer=False)
    served_while_processing = luna_overlay.apply_cached_enrichment([replacement])

    assert served_while_processing[0].title == "Uge 34"
    assert len(served_while_processing[0].structured_offers) == 1
    assert served_while_processing[0].structured_offers[0].hotspot_width == 0.3


def test_ready_replacement_atomically_replaces_cached_generation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    state = {"ready": False}
    monkeypatch.setattr(
        luna_overlay,
        "publication_is_ready",
        lambda publication: state["ready"],
    )

    original = _publication(title="Uge 34", price=15.0, with_offer=True)
    luna_overlay.apply_cached_enrichment([original])

    replacement = _publication(title="Uge 35", price=12.0, with_offer=True)
    replacement.structured_offers[0] = replacement.structured_offers[0].model_copy(
        update={"product_name": "Ny vare", "hotspot_x": 0.7}
    )

    state["ready"] = True
    served = luna_overlay.apply_cached_enrichment([replacement])

    assert served[0].title == "Uge 35"
    assert served[0].structured_offers[0].product_name == "Ny vare"
    assert served[0].structured_offers[0].hotspot_x == 0.7

    cache = json.loads((tmp_path / "flyer-serving-cache.json").read_text("utf-8"))
    assert cache["publications"]["rema-week"]["verified"] is True


def test_verified_snapshot_survives_temporary_provider_gap(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: True)

    original = _publication()
    luna_overlay.apply_cached_enrichment([original])

    served = luna_overlay.apply_cached_enrichment([])
    assert len(served) == 1
    assert served[0].retailer == "REMA 1000"
    assert len(served[0].structured_offers) == 1

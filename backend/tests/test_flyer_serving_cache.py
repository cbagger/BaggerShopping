import json

from app import luna_overlay
from app.meny_flyer import Offer, Publication
from app.offer_serialization import customer_offer_payload


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


def _seafood_publication():
    publication = Publication(
        id="foetex-week",
        retailer="føtex",
        title="føtex uge 34",
        valid_from="14.08.2026",
        valid_until="20.08.2026",
        status="current",
        source_url="https://example.test/foetex",
        page_count=1,
        page_image_urls=["https://example.test/foetex/page-1.jpg"],
    )
    publication.structured_offers = [Offer(
        id="seafoodmix",
        retailer="føtex",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="Salling Seafoodmix, vannameirejer, tunsteak eller -poke",
        price=29.0,
        normal_price=166.67,
        source_url=publication.source_url,
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        hotspot_confidence=0.95,
        raw_text=(
            "Salling Seafoodmix, vannameirejer, tunsteak eller -poke 150-300 g. "
            "PLUS PRIS 25,- Gælder kun med føtex Plus appen. "
            "PR. STK. 29,- Pr. kg max. 193,33"
        ),
    )]
    return publication


def test_processing_replacement_keeps_last_verified_hotspots(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    state = {"ready": True}
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: state["ready"])

    first = _publication(title="Uge 34", price=15.0, with_offer=True)
    served = luna_overlay.apply_cached_enrichment([first])
    assert len(served) == 1
    assert len(served[0].structured_offers) == 1
    assert served[0].structured_offers[0].hotspot_x == 0.1

    state["ready"] = False
    replacement = _publication(title="Uge 35", price=12.0, with_offer=False)
    served_while_processing = luna_overlay.apply_cached_enrichment([replacement])

    assert served_while_processing[0].title == "Uge 34"
    assert len(served_while_processing[0].structured_offers) == 1
    assert served_while_processing[0].structured_offers[0].hotspot_width == 0.3


def test_brand_new_unverified_publication_is_not_customer_visible(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: False)

    upcoming = _publication(title="Ny weekendavis", price=10.0, with_offer=True)
    served = luna_overlay.apply_cached_enrichment([upcoming])

    assert served == []
    cache = json.loads((tmp_path / "flyer-serving-cache.json").read_text("utf-8"))
    row = cache["publications"][upcoming.id]
    assert row["verified"] is False
    assert row["publication"]["structured_offers"][0]["price"] == 10.0

    served_again = luna_overlay.apply_cached_enrichment([upcoming])
    assert served_again == []


def test_ready_replacement_atomically_replaces_cached_generation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    state = {"ready": False}
    monkeypatch.setattr(
        luna_overlay,
        "publication_is_ready",
        lambda publication: state["ready"],
    )

    original = _publication(title="Uge 34", price=15.0, with_offer=True)
    assert luna_overlay.apply_cached_enrichment([original]) == []

    state["ready"] = True
    replacement = _publication(title="Uge 35", price=12.0, with_offer=True)
    replacement.structured_offers[0] = replacement.structured_offers[0].model_copy(
        update={"product_name": "Ny vare", "hotspot_x": 0.7}
    )

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


def test_retired_retailer_snapshot_is_pruned_instead_of_served(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: True)

    retired = _publication()
    retired.retailer = "Kvickly"
    cache_path = tmp_path / "flyer-serving-cache.json"
    cache_path.write_text(json.dumps({
        "version": 2,
        "publications": {
            retired.id: luna_overlay._publication_snapshot(retired, verified=True),
        },
    }), encoding="utf-8")

    assert luna_overlay.apply_cached_enrichment([]) == []
    cache = json.loads(cache_path.read_text("utf-8"))
    assert retired.id not in cache["publications"]


def test_snapshot_stores_raw_offer_fields_not_member_price_presentation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    publication = _seafood_publication()
    offer = publication.structured_offers[0]

    raw = offer.model_dump()
    assert raw["price"] == 29.0
    assert "member_price" not in raw

    presented = customer_offer_payload(offer)
    assert presented["price"] == 29.0
    assert presented["member_price"] == 25.0

    snapshot = luna_overlay._publication_snapshot(publication, verified=True)
    cached_offer = snapshot["publication"]["structured_offers"][0]

    assert snapshot["content_revision"] == luna_overlay._SERVING_CACHE_CONTENT_REVISION
    assert cached_offer["price"] == 29.0
    assert cached_offer["normal_price"] == 166.67
    assert "member_price" not in cached_offer
    assert "member_price_label" not in cached_offer


def test_v1_cache_is_rebuilt_from_current_raw_provider_generation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    cache_path = tmp_path / "flyer-serving-cache.json"

    poisoned = _seafood_publication()
    poisoned_payload = poisoned.model_dump(exclude={"text", "page_texts"})
    poisoned_payload["structured_offers"] = [
        {
            **luna_overlay._raw_offer_payload(poisoned.structured_offers[0]),
            "price": None,
        }
    ]
    cache_path.write_text(json.dumps({
        "version": 1,
        "publications": {
            poisoned.id: {
                "fingerprint": "legacy-poisoned",
                "verified": True,
                "saved_at": 1,
                "publication": poisoned_payload,
            }
        },
    }), encoding="utf-8")

    current = _seafood_publication()
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: True)

    served = luna_overlay.apply_cached_enrichment([current])

    assert served[0].structured_offers[0].price == 29.0
    rewritten = json.loads(cache_path.read_text("utf-8"))
    assert rewritten["version"] == 2
    cached_offer = rewritten["publications"][current.id]["publication"]["structured_offers"][0]
    assert cached_offer["price"] == 29.0
    assert "member_price" not in cached_offer


def test_verified_same_source_is_rewritten_when_content_revision_changes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    cache_path = tmp_path / "flyer-serving-cache.json"
    monkeypatch.setattr(luna_overlay, "publication_is_ready", lambda publication: True)

    current = _publication()
    old_snapshot = luna_overlay._publication_snapshot(current, verified=True)
    old_snapshot.pop("content_revision", None)
    old_snapshot["publication"]["structured_offers"][0]["raw_text"] = "stale deterministic parser text"
    cache_path.write_text(json.dumps({
        "version": 2,
        "publications": {current.id: old_snapshot},
    }), encoding="utf-8")

    served = luna_overlay.apply_cached_enrichment([current])

    assert served[0].structured_offers[0].raw_text == "Kohberg brød 15 kr"
    rewritten = json.loads(cache_path.read_text("utf-8"))
    row = rewritten["publications"][current.id]
    assert row["content_revision"] == luna_overlay._SERVING_CACHE_CONTENT_REVISION
    assert row["publication"]["structured_offers"][0]["raw_text"] == "Kohberg brød 15 kr"

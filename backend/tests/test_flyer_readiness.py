from __future__ import annotations

import json

from app import flyer_readiness as readiness
from app.meny_flyer import Offer, OfferVariant, Publication


def publication(
    identifier: str,
    *,
    image: str = "https://cdn.test/page.jpg?token=one",
    price: float = 15,
    valid_from: str = "14.08.2026",
    valid_until: str = "20.08.2026",
) -> Publication:
    offer = Offer(
        id="offer-1",
        retailer="Bilka",
        publication_id=identifier,
        publication_title="Uge 34",
        product_name="Becel flydende",
        price=price,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        raw_text=f"Becel flydende {price:g} kr",
        quality_score=0.99,
        hotspot_confidence=0.99,
    )
    return Publication(
        id=identifier,
        retailer="Bilka",
        title="Uge 34",
        valid_from=valid_from,
        valid_until=valid_until,
        status="current",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=[image],
        structured_offers=[offer],
    )


def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_READINESS_STORE_PATH", str(tmp_path / "readiness.json"))


def test_bootstrap_existing_publications_are_ready(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    existing = publication("existing")
    result = readiness.observe_publications([existing], bootstrap_ready_ids=None)
    assert result["queued"] == []
    assert readiness.publication_is_ready(existing) is True
    assert readiness.status_payload()["counts"] == {"ready": 1}
    assert readiness.status_payload()["version"] == 2


def test_new_publication_after_bootstrap_is_processing_until_mark_ready(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    existing = publication("existing")
    readiness.observe_publications([existing], bootstrap_ready_ids=None)

    new = publication("new")
    result = readiness.observe_publications([existing, new], bootstrap_ready_ids={"existing"})
    assert result["queued"] == ["new"]
    assert readiness.publication_is_ready(existing) is True
    assert readiness.publication_is_ready(new) is False
    assert [row["publication_id"] for row in readiness.pending_publication_records()] == ["new"]

    assert readiness.mark_ready(new) is True
    assert readiness.publication_is_ready(new) is True


def test_parser_changes_do_not_reopen_known_release(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    original = publication("same")
    readiness.observe_publications([original], bootstrap_ready_ids=None)

    changed = original.model_copy(deep=True)
    changed.structured_offers[0].price = 12
    changed.structured_offers[0].raw_text = "Ny parsertekst PLUS pris 9 kr"
    changed.structured_offers[0].quality_signals = ["member-price-context-nearby-v3"]
    changed.structured_offers[0].variants = [
        OfferVariant(id="v1", name="Becel Original 500 ml")
    ]

    assert readiness.publication_fingerprint(changed) == readiness.publication_fingerprint(original)
    result = readiness.observe_publications([changed], bootstrap_ready_ids={"same"})
    assert result["queued"] == []
    assert result["changed"] == []
    assert readiness.publication_is_ready(changed) is True


def test_changed_source_image_path_reopens_only_relevant_page(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    original = publication("same", image="https://cdn.test/page-a.jpg?token=one")
    readiness.observe_publications([original], bootstrap_ready_ids=None)

    changed = publication("same", image="https://cdn.test/page-b.jpg?token=two")
    assert readiness.publication_is_ready(changed) is False

    result = readiness.observe_publications([changed], bootstrap_ready_ids={"same"})
    assert result["changed"] == ["same"]
    pending = readiness.pending_publication_records()[0]
    assert pending["changed_pages"] == [1]


def test_release_date_change_with_reused_page_url_reopens_full_release(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    original = publication("same")
    readiness.observe_publications([original], bootstrap_ready_ids=None)

    changed = publication(
        "same",
        valid_from="21.08.2026",
        valid_until="27.08.2026",
    )
    result = readiness.observe_publications([changed], bootstrap_ready_ids={"same"})
    assert result["changed"] == ["same"]
    assert readiness.pending_publication_records()[0]["changed_pages"] == [1]


def test_signed_image_query_rotation_does_not_create_new_version(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    first = publication("same", image="https://cdn.test/page.jpg?token=one")
    second = publication("same", image="https://cdn.test/page.jpg?token=two")
    readiness.observe_publications([first], bootstrap_ready_ids=None)
    result = readiness.observe_publications([second], bootstrap_ready_ids={"same"})
    assert result["queued"] == []
    assert result["changed"] == []
    assert readiness.publication_is_ready(second) is True


def test_v1_migration_does_not_replay_known_same_release(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication("known")
    path = readiness.store_path()
    path.write_text(
        json.dumps({
            "version": 1,
            "initialized": True,
            "updated_at": 1,
            "publications": {
                "known": {
                    "publication_id": "known",
                    "retailer": "Bilka",
                    "title": "Uge 34",
                    "valid_from": "14.08.2026",
                    "valid_until": "20.08.2026",
                    "fingerprint": "legacy-derived-parser-fingerprint",
                    "page_fingerprints": {"1": "legacy-page"},
                    "status": "processing",
                    "changed_pages": [1],
                    "attempts": 17,
                    "last_error": "completed",
                }
            },
        }),
        encoding="utf-8",
    )

    assert readiness.readiness_store_version() == 1
    result = readiness.observe_publications([flyer], bootstrap_ready_ids={"known"})
    assert result["migrated"] == ["known"]
    assert result["queued"] == []
    assert readiness.readiness_store_version() == 2
    assert readiness.pending_publication_records() == []
    assert readiness.publication_is_ready(flyer) is True


def test_v1_migration_still_queues_truly_new_release(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    old = publication("old")
    path = readiness.store_path()
    path.write_text(
        json.dumps({
            "version": 1,
            "initialized": True,
            "publications": {
                "old": {
                    "publication_id": "old",
                    "valid_from": old.valid_from,
                    "valid_until": old.valid_until,
                    "fingerprint": "legacy",
                    "page_fingerprints": {"1": "legacy"},
                    "status": "ready",
                }
            },
        }),
        encoding="utf-8",
    )

    new = publication("new")
    result = readiness.observe_publications([old, new], bootstrap_ready_ids={"old"})
    assert "new" in result["queued"]
    assert [row["publication_id"] for row in readiness.pending_publication_records()] == ["new"]


def test_unknown_publication_fails_shut_after_initialization(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    readiness.observe_publications([publication("known")], bootstrap_ready_ids=None)
    assert readiness.publication_is_ready(publication("unknown")) is False

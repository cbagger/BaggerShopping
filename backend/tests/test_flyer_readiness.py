from __future__ import annotations

from app import flyer_readiness as readiness
from app.meny_flyer import Offer, Publication


def publication(identifier: str, *, image: str = "https://cdn.test/page.jpg?token=one", price: float = 15) -> Publication:
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
        valid_from="14.08.2026",
        valid_until="20.08.2026",
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


def test_changed_same_id_reopens_gate_and_tracks_changed_page(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    original = publication("same")
    readiness.observe_publications([original], bootstrap_ready_ids=None)
    assert readiness.publication_is_ready(original)

    changed = publication("same", price=12)
    result = readiness.observe_publications([changed], bootstrap_ready_ids={"same"})
    assert result["changed"] == ["same"]
    assert readiness.publication_is_ready(changed) is False
    pending = readiness.pending_publication_records()[0]
    assert pending["changed_pages"] == [1]


def test_signed_image_query_rotation_does_not_create_new_version(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    first = publication("same", image="https://cdn.test/page.jpg?token=one")
    second = publication("same", image="https://cdn.test/page.jpg?token=two")
    readiness.observe_publications([first], bootstrap_ready_ids=None)
    result = readiness.observe_publications([second], bootstrap_ready_ids={"same"})
    assert result["queued"] == []
    assert result["changed"] == []
    assert readiness.publication_is_ready(second) is True


def test_unknown_publication_fails_shut_after_initialization(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    readiness.observe_publications([publication("known")], bootstrap_ready_ids=None)
    assert readiness.publication_is_ready(publication("unknown")) is False

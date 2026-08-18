import json
from types import SimpleNamespace

import pytest

from app import luna_resilient_worker as worker
from app.flyer_readiness import STORE_VERSION, publication_fingerprint
from app.meny_flyer import Offer, Publication


def _publication(*, image="https://images.test/page-1.jpg"):
    offer = Offer(
        id="offer-1",
        retailer="Lidl",
        publication_id="lidl-34",
        publication_title="Weekendavis (uge 34)",
        product_name="KIMS Peanuts",
        price=59,
        source_url="https://example.test/lidl",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text="KIMS Peanuts 1 kg. Pr. kg 59,00 Spot¹",
    )
    return Publication(
        id="lidl-34",
        retailer="Lidl",
        title="Weekendavis (uge 34)",
        source_url="https://example.test/lidl",
        page_count=1,
        page_image_urls=[image],
        structured_offers=[offer],
    )


def _crop_candidate(publication, *, raw_text=None, price=None, reasons=None):
    offer = publication.structured_offers[0]
    if raw_text is not None:
        offer.raw_text = raw_text
    if price is not None:
        offer.price = price
    return SimpleNamespace(
        fingerprint="crop-1",
        publication=publication,
        offer=offer,
        reasons=tuple(reasons or ("page-audit-ordinary-price-is-unit-price",)),
    )


@pytest.mark.asyncio
async def test_pending_publication_is_released_before_any_luna_enrichment(monkeypatch):
    publication = _publication()
    record = {
        "publication_id": publication.id,
        "fingerprint": publication_fingerprint(publication),
        "changed_pages": [1],
    }
    calls = []

    monkeypatch.setattr(worker, "readiness_store_version", lambda: STORE_VERSION)
    monkeypatch.setattr(worker, "pending_publication_records", lambda: [record])
    monkeypatch.setattr(worker, "mark_ready", lambda value: calls.append(value.id) or True)
    monkeypatch.setattr(worker, "_clear_legacy_publication_stall", lambda publication_id: 1)

    result = await worker._publish_pending_once([publication])

    assert result["status"] == "published"
    assert result["publication_id"] == publication.id
    assert result["legacy_stalls_removed"] == 1
    assert calls == [publication.id]


def test_one_kg_provider_price_equal_to_per_kg_is_not_paid_conflict():
    publication = _publication()
    candidate = _crop_candidate(publication)

    assert worker._provider_unit_equivalence(candidate)


def test_non_equivalent_package_stays_eligible_for_visual_review():
    publication = _publication()
    candidate = _crop_candidate(
        publication,
        raw_text="KIMS Peanuts 500 g. Pr. kg 59,00 Spot¹",
    )

    assert not worker._provider_unit_equivalence(candidate)


def test_genuine_member_reason_is_never_suppressed_by_unit_equivalence():
    publication = _publication()
    candidate = _crop_candidate(
        publication,
        reasons=(
            "page-audit-ordinary-price-is-unit-price",
            "page-audit-new-member-price-verification",
        ),
    )

    assert not worker._provider_unit_equivalence(candidate)


def test_quarantine_is_scoped_to_source_generation_and_contract(monkeypatch, tmp_path):
    quarantine = tmp_path / "quarantine.json"
    monkeypatch.setattr(worker, "_quarantine_path", lambda: quarantine)

    publication = _publication()
    candidate = _crop_candidate(publication)
    worker._quarantine("pricing", publication, candidate, "ambiguous")

    assert worker._is_quarantined("pricing", publication, candidate)

    next_generation = _publication(image="https://images.test/page-1-v2.jpg")
    next_candidate = _crop_candidate(next_generation)
    assert not worker._is_quarantined("pricing", next_generation, next_candidate)

    payload = json.loads(quarantine.read_text("utf-8"))
    assert payload["contract"] == worker.RESILIENCE_CONTRACT_VERSION
    assert len(payload["items"]) == 1


def test_legacy_publication_stall_is_removed_without_touching_other_publications(monkeypatch, tmp_path):
    stall_path = tmp_path / "stalls.json"
    stall_path.write_text(
        json.dumps(
            {
                "version": 1,
                "stalled": {
                    "lidl": {"publication_id": "lidl-34", "error": "old"},
                    "other": {"publication_id": "other-34", "error": "keep"},
                },
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(worker, "_stalled_publications_path", lambda: stall_path)

    removed = worker._clear_legacy_publication_stall("lidl-34")

    assert removed == 1
    payload = json.loads(stall_path.read_text("utf-8"))
    assert list(payload["stalled"]) == ["other"]

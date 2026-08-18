from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app import flyer_readiness as readiness
from app import luna_worker
from app.meny_flyer import Offer, Publication


def publication(identifier: str = "new", *, pages: int = 1) -> Publication:
    offers = []
    images = []
    for page in range(1, pages + 1):
        images.append(f"https://example.test/page-{page}.jpg")
        offers.append(Offer(
            id=f"offer-{page}",
            retailer="MENY",
            publication_id=identifier,
            publication_title="MENY uge 35",
            product_name=f"Testvare {page}",
            price=15,
            source_url="https://example.test",
            page_number=page,
            hotspot_x=0.1,
            hotspot_y=0.2,
            hotspot_width=0.3,
            hotspot_height=0.2,
            raw_text=f"Testvare {page} 15 kr",
            quality_score=0.99,
            hotspot_confidence=0.99,
        ))
    return Publication(
        id=identifier,
        retailer="MENY",
        title="MENY uge 35",
        valid_from="21.08.2026",
        valid_until="27.08.2026",
        status="current",
        source_url="https://example.test",
        page_count=pages,
        page_image_urls=images,
        structured_offers=offers,
    )


def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_READINESS_STORE_PATH", str(tmp_path / "readiness.json"))
    monkeypatch.setenv("LUNA_EXECUTION_LOCK_PATH", str(tmp_path / "luna-execution.lock"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(luna_worker, "load_config", lambda: {"enabled": True})


def test_idle_queue_does_not_fetch_retailers(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)

    async def must_not_fetch():
        raise AssertionError("idle queue must not poll providers")

    monkeypatch.setattr(luna_worker, "fetch_all_publications", must_not_fetch)
    result = asyncio.run(luna_worker.run_queued_once())
    assert result["status"] == "idle"
    assert result["queue_depth"] == 0


def test_legacy_readiness_queue_cannot_spend_before_migration(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication()
    readiness.store_path().write_text(json.dumps({
        "version": 1,
        "initialized": True,
        "publications": {
            flyer.id: {
                "publication_id": flyer.id,
                "fingerprint": "legacy",
                "status": "processing",
                "changed_pages": [1],
            }
        },
    }), encoding="utf-8")

    async def must_not_fetch():
        raise AssertionError("v1 queue must wait for source-fingerprint migration")

    monkeypatch.setattr(luna_worker, "fetch_all_publications", must_not_fetch)
    result = asyncio.run(luna_worker.run_queued_once())
    assert result["status"] == "readiness-migration-pending"


def test_queued_publication_is_marked_ready_after_mandatory_work_drains(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication()
    readiness.observe_publications([flyer], bootstrap_ready_ids=set())
    assert readiness.publication_is_ready(flyer) is False

    async def fetch(): return [flyer]
    monkeypatch.setattr(luna_worker, "fetch_all_publications", fetch)
    monkeypatch.setattr(luna_worker, "collect_page_audit_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker, "collect_crop_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker, "collect_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker._cost_policy, "status_payload", lambda: {})

    result = asyncio.run(luna_worker.run_queued_once())
    assert result["status"] == "ready"
    assert result["publication_id"] == "new"
    assert result["changed_pages"] == [1]
    assert readiness.publication_is_ready(flyer) is True
    assert readiness.pending_publication_records() == []


def test_failed_page_audit_keeps_publication_hidden(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication()
    readiness.observe_publications([flyer], bootstrap_ready_ids=set())

    async def fetch(): return [flyer]
    monkeypatch.setattr(luna_worker, "fetch_all_publications", fetch)

    @dataclass
    class Candidate:
        page_number: int = 1

    monkeypatch.setattr(luna_worker, "collect_page_audit_candidates", lambda values: [Candidate()])
    monkeypatch.setattr(luna_worker, "collect_crop_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker, "collect_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker._cost_policy, "status_payload", lambda: {})

    async def failed(candidate, client=None):
        return {"status": "failed", "error": "vision failed"}

    monkeypatch.setattr(luna_worker, "analyze_page_audit", failed)
    result = asyncio.run(luna_worker.run_queued_once())
    assert result["status"] == "page-audit-failed"
    assert readiness.publication_is_ready(flyer) is False
    pending = readiness.pending_publication_records()
    assert len(pending) == 1
    assert pending[0]["last_error"] == "vision failed"


def test_worker_only_analyzes_pages_listed_as_changed(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication(pages=2)
    readiness.observe_publications([flyer], bootstrap_ready_ids=None)

    changed = flyer.model_copy(deep=True)
    changed.page_image_urls[1] = "https://example.test/page-2-revised.jpg"
    readiness.observe_publications([changed], bootstrap_ready_ids={changed.id})
    pending = readiness.pending_publication_records()[0]
    assert pending["changed_pages"] == [2]

    async def fetch(): return [changed]
    monkeypatch.setattr(luna_worker, "fetch_all_publications", fetch)

    @dataclass
    class PageCandidate:
        page_number: int

    analyzed: list[int] = []
    monkeypatch.setattr(
        luna_worker,
        "collect_page_audit_candidates",
        lambda values: [PageCandidate(1), PageCandidate(2)] if not analyzed else [],
    )
    monkeypatch.setattr(luna_worker, "collect_crop_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker, "collect_candidates", lambda values: [])
    monkeypatch.setattr(luna_worker._cost_policy, "status_payload", lambda: {})

    async def completed(candidate, client=None):
        analyzed.append(candidate.page_number)
        return {"status": "completed"}

    monkeypatch.setattr(luna_worker, "analyze_page_audit", completed)
    result = asyncio.run(luna_worker.run_queued_once())
    assert result["status"] == "ready"
    assert result["changed_pages"] == [2]
    assert analyzed == [2]


def test_execution_lease_prevents_duplicate_paid_queue_runner(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication()
    readiness.observe_publications([flyer], bootstrap_ready_ids=set())

    with luna_worker._execution_lease() as acquired:
        assert acquired is True
        result = asyncio.run(luna_worker.run_queued_once())

    assert result["status"] == "busy"
    assert readiness.pending_publication_records()


def test_mandatory_pricing_crop_requires_pricing_confidence_not_identity_only():
    config = {"min_apply_confidence": 0.96}
    result = {
        "status": "completed",
        "semantic_facts": {
            "visible": True,
            "ordinary_price": 15,
            "member_price": 12,
            "identity_confidence": 1.0,
            "pricing_confidence": 0.70,
        },
    }
    assert luna_worker._mandatory_pricing_crop_verified(result, config) is False

    result["semantic_facts"]["pricing_confidence"] = 0.99
    assert luna_worker._mandatory_pricing_crop_verified(result, config) is True


def test_mandatory_pricing_crop_rejects_invalid_member_relation():
    result = {
        "status": "completed",
        "semantic_facts": {
            "visible": True,
            "ordinary_price": 12,
            "member_price": 15,
            "pricing_confidence": 0.99,
        },
    }
    assert luna_worker._mandatory_pricing_crop_verified(
        result, {"min_apply_confidence": 0.96}
    ) is False

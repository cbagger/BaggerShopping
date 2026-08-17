from __future__ import annotations

import asyncio

from app import flyer_readiness as readiness
from app import luna_worker
from app.meny_flyer import Publication


def publication(identifier: str = "new") -> Publication:
    return Publication(
        id=identifier,
        retailer="MENY",
        title="MENY uge 35",
        valid_from="21.08.2026",
        valid_until="27.08.2026",
        status="current",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://example.test/page.jpg"],
    )


def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_READINESS_STORE_PATH", str(tmp_path / "readiness.json"))
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
    assert readiness.publication_is_ready(flyer) is True
    assert readiness.pending_publication_records() == []


def test_failed_page_audit_keeps_publication_hidden(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    flyer = publication()
    readiness.observe_publications([flyer], bootstrap_ready_ids=set())

    async def fetch(): return [flyer]
    monkeypatch.setattr(luna_worker, "fetch_all_publications", fetch)
    monkeypatch.setattr(luna_worker, "collect_page_audit_candidates", lambda values: [object()])
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

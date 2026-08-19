from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import control_center
from app import control_center_catalog
from app import control_center_snapshot as snapshot
from app import control_telemetry
from app import luna_member_coverage


def test_control_center_accepts_only_local_network_clients():
    for host in ("127.0.0.1", "::1", "192.168.0.111", "10.20.30.40", "172.20.0.3", "testclient"):
        assert control_center._client_is_local(host) is True
    for host in ("8.8.8.8", "1.1.1.1", "example.com", None):
        assert control_center._client_is_local(host) is False


def test_architecture_catalog_is_complete_and_referentially_valid():
    components = control_center_catalog.catalog()
    identifiers = [row["id"] for row in components]
    assert len(identifiers) == len(set(identifiers))
    assert {
        "core-api", "mobile-api", "control-center", "luna-worker", "flyer-push-worker",
        "samsung-food", "openai-luna", "flyer-readiness", "member-pricing",
        "member-coverage", "product-identity", "variant-engine", "smart-offer-matching",
        "geofence-engine",
    }.issubset(identifiers)
    known = set(identifiers)
    for row in components:
        assert row["name"] and row["description"] and row["code"]
        assert set(row.get("depends_on", ())).issubset(known)
    for edge in control_center_catalog.dataflow():
        assert edge["from"] in known and edge["to"] in known and edge["label"]


def test_household_summary_is_sanitized(monkeypatch):
    secret_hash = "token-hash-must-never-leak"
    household_id = "family-bagger"
    monkeypatch.setattr(snapshot, "load_households", lambda: {
        "households": {
            household_id: {
                "id": household_id,
                "name": "Familien Test",
                "list_backend": "samsung",
                "owner": {"id": "legacy-owner", "name": "Owner", "role": "owner"},
                "members": {secret_hash: {"id": "private-member-id", "name": "Member", "role": "member"}},
                "items": [],
                "offer_metadata": {"a": {}},
                "product_preferences": {"mælk": {}},
                "recovery_code_hash": "private-recovery-hash",
                "integrations": {"samsung_food": {"status": "connected", "last_successful_sync": 123456, "private": "do-not-copy"}},
            }
        },
        "invites": {"invite-secret": {"household_id": household_id}},
    })
    summary = snapshot.summarize_households()
    encoded = json.dumps(summary, ensure_ascii=False)
    assert summary["households"] == 1
    assert summary["members"] == 2
    assert summary["records"][0]["name"] == "Familien Test"
    assert summary["records"][0]["samsung_status"] == "connected"
    for secret in (secret_hash, "private-member-id", "private-recovery-hash", "invite-secret", household_id):
        assert secret not in encoded


def _source(publication_id: str, fingerprint: str, *, valid_until: str = "31.12.2099") -> dict:
    return {
        "publications": {
            publication_id: {
                "publication_id": publication_id,
                "retailer": "Netto",
                "title": "Netto uge 35",
                "valid_from": "20.08.2026",
                "valid_until": valid_until,
                "fingerprint": fingerprint,
                "page_fingerprints": {"1": "a", "2": "b", "3": "c", "4": "d"},
                "status": "ready",
                "detected_at": 100,
                "ready_at": 110,
            }
        }
    }


def _stub_current_publication_dependencies(monkeypatch, source: dict):
    monkeypatch.setattr(snapshot.flyer_readiness, "load_store", lambda: source)
    monkeypatch.setattr(snapshot.luna_resilient_worker, "_load_quarantine", lambda: {})
    monkeypatch.setattr(snapshot.luna_resilient_strong_worker, "_load_retry_state", lambda: {})
    monkeypatch.setattr(snapshot.luna_overlay, "_load_serving_cache", lambda: {"publications": {}})
    monkeypatch.setattr(snapshot, "_exact_luna_stats", lambda *args, **kwargs: {"available": False, "records": 0, "failed": 0, "member_prices": None})


def test_publication_coverage_uses_exact_source_generation(monkeypatch):
    publication_id = "netto-35"
    current_fingerprint = "source-current"
    old_fingerprint = "source-old"
    _stub_current_publication_dependencies(monkeypatch, _source(publication_id, current_fingerprint))

    monkeypatch.setattr(snapshot.luna_member_coverage, "_load", lambda: {
        "items": {
            luna_member_coverage.coverage_key(publication_id, old_fingerprint): {
                "publication_id": publication_id,
                "fingerprint": old_fingerprint,
                "status": "complete",
                "pages_remaining": 0,
            }
        }
    })
    rows, counts = snapshot.current_publications({})
    assert rows[0]["coverage_status"] == "not_tracked"
    assert rows[0]["progress"] == 0
    assert counts["not_tracked"] == 1

    monkeypatch.setattr(snapshot.luna_member_coverage, "_load", lambda: {
        "items": {
            luna_member_coverage.coverage_key(publication_id, current_fingerprint): {
                "publication_id": publication_id,
                "fingerprint": current_fingerprint,
                "status": "pending",
                "pages_remaining": 1,
                "pricing_remaining": 0,
                "member_fallback_remaining": 2,
                "hard_quarantined": 0,
                "updated_at": 120,
            }
        }
    })
    monkeypatch.setattr(snapshot, "_exact_luna_stats", lambda *args, **kwargs: {"available": True, "records": 7, "failed": 1, "member_prices": 3})
    rows, counts = snapshot.current_publications({})
    assert rows[0]["coverage_status"] == "pending"
    assert rows[0]["pages_done"] == 3
    assert rows[0]["progress"] == 75
    assert rows[0]["member_fallback_remaining"] == 2
    assert rows[0]["member_prices_verified"] == 3
    assert rows[0]["luna_generation_stats_available"] is True
    assert counts["pending"] == 1


def test_expired_readiness_rows_do_not_pollute_current_coverage(monkeypatch):
    _stub_current_publication_dependencies(monkeypatch, _source("old", "fingerprint", valid_until="01.01.2020"))
    monkeypatch.setattr(snapshot.luna_member_coverage, "_load", lambda: {"items": {}})
    rows, counts = snapshot.current_publications({})
    assert rows == []
    assert sum(counts.values()) == 0


def test_exact_luna_member_stats_require_matching_verified_serving_generation(monkeypatch):
    offer = SimpleNamespace(id="offer-1")
    publication = SimpleNamespace(structured_offers=[offer])
    monkeypatch.setattr(snapshot.luna_overlay, "_restore_publication", lambda row: publication)
    monkeypatch.setattr(snapshot.luna_enrichment, "offer_fingerprint", lambda value: "offer-fingerprint")
    store = {"records": {"offer-fingerprint": {"status": "completed", "facts": {"member_price": 12.0}}}}

    assert snapshot._exact_luna_stats("pub", "current", luna_store=store, serving_rows={})["member_prices"] is None
    assert snapshot._exact_luna_stats(
        "pub", "current", luna_store=store,
        serving_rows={"pub": {"fingerprint": "old", "verified": True}},
    )["member_prices"] is None
    result = snapshot._exact_luna_stats(
        "pub", "current", luna_store=store,
        serving_rows={"pub": {"fingerprint": "current", "verified": True}},
    )
    assert result["available"] is True
    assert result["member_prices"] == 1
    assert result["records"] == 1


def test_component_state_reports_budget_samsung_and_degraded_coverage():
    runtime = {
        "core-api": {"health": "healthy", "state": "online", "latency_ms": 5, "payload": {}},
        "mobile-api": {"health": "healthy", "state": "online", "latency_ms": 4, "payload": {}},
        "samsung-login-broker": {"health": "healthy", "state": "online", "latency_ms": 3, "payload": {}},
        "luna-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {}},
        "flyer-push-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {"last_provider_check_at": 9999999999}},
        "shopping-cleanup-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {}},
    }
    states = snapshot.derive_component_states(
        runtime,
        samsung={"ok": True, "payload": {"samsung_auth": "ok"}},
        luna={"enabled": True, "api_key_configured": True, "usage": {"estimated_cost_dkk": 25.0, "budget_dkk": 100.0, "remaining_dkk": 75.0}},
        flyer_push_store={"devices": {"phone": {"enabled": True}}},
        household_summary={"households": 1},
        current_coverage={"pending": 1, "complete": 4, "degraded": 2},
    )
    by_id = {row["id"]: row for row in states}
    assert by_id["openai-luna"]["health"] == "healthy"
    assert by_id["samsung-food"]["state"] == "connected"
    assert by_id["member-coverage"]["health"] == "attention"
    assert by_id["apple-apns"]["state"] == "configured"
    assert by_id["geofence-engine"]["state"] == "deployed"


@pytest.mark.asyncio
async def test_samsung_probe_is_cached_and_not_tied_to_realtime_refresh(monkeypatch):
    calls = []

    async def fake_probe(name, url):
        calls.append((name, url))
        return {"name": name, "ok": True, "payload": {"samsung_auth": "ok"}}

    monkeypatch.setattr(snapshot, "_probe_json", fake_probe)
    monkeypatch.setattr(snapshot, "_samsung_probe_cache", None)
    monkeypatch.setattr(snapshot, "_samsung_probe_at", 0.0)
    first = await snapshot.samsung_probe()
    second = await snapshot.samsung_probe()
    assert first == second
    assert len(calls) == 1


def test_control_center_static_shell_is_light_local_and_read_only():
    client = TestClient(control_center.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["local_only"] is True
    assert health.json()["read_only"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "Kurv Control Center" in page.text
    assert 'name="color-scheme" content="light"' in page.text
    assert "dark" not in page.text.casefold()


def test_telemetry_heartbeat_roundtrip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KURV_TELEMETRY_DIR", str(tmp_path / "heartbeats"))
    control_telemetry.write_heartbeat("luna-worker", status="running", detail="Netto uge 35", metrics={"pending": 3})
    row = control_telemetry.read_heartbeat("luna-worker", stale_after=60)
    assert row["status"] == "running"
    assert row["detail"] == "Netto uge 35"
    assert row["metrics"] == {"pending": 3}
    assert row["stale"] is False
    assert control_telemetry.all_heartbeats()["luna-worker"]["status"] == "running"

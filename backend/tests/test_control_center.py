from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import control_center
from app import control_center_catalog
from app import control_telemetry
from app import luna_member_coverage


def test_control_center_accepts_only_local_network_clients():
    assert control_center._client_is_local("127.0.0.1") is True
    assert control_center._client_is_local("::1") is True
    assert control_center._client_is_local("192.168.0.111") is True
    assert control_center._client_is_local("10.20.30.40") is True
    assert control_center._client_is_local("172.20.0.3") is True
    assert control_center._client_is_local("testclient") is True
    assert control_center._client_is_local("8.8.8.8") is False
    assert control_center._client_is_local("1.1.1.1") is False
    assert control_center._client_is_local("example.com") is False
    assert control_center._client_is_local(None) is False


def test_architecture_catalog_is_complete_and_referentially_valid():
    components = control_center_catalog.catalog()
    identifiers = [row["id"] for row in components]
    assert len(identifiers) == len(set(identifiers))
    required = {
        "core-api",
        "mobile-api",
        "control-center",
        "luna-worker",
        "flyer-push-worker",
        "samsung-food",
        "openai-luna",
        "flyer-readiness",
        "member-pricing",
        "member-coverage",
        "product-identity",
        "variant-engine",
        "smart-offer-matching",
        "geofence-engine",
    }
    assert required.issubset(identifiers)
    known = set(identifiers)
    for row in components:
        assert row["name"]
        assert row["description"]
        assert row["code"]
        assert set(row.get("depends_on", ())).issubset(known)

    for edge in control_center_catalog.dataflow():
        assert edge["from"] in known
        assert edge["to"] in known
        assert edge["label"]


def test_household_summary_is_sanitized(monkeypatch):
    secret_hash = "token-hash-must-never-leak"
    household_id = "family-bagger"
    monkeypatch.setattr(
        control_center,
        "load_households",
        lambda: {
            "households": {
                household_id: {
                    "id": household_id,
                    "name": "Familien Test",
                    "list_backend": "samsung",
                    "owner": {"id": "legacy-owner", "name": "Owner", "role": "owner"},
                    "members": {
                        secret_hash: {
                            "id": "private-member-id",
                            "name": "Member",
                            "role": "member",
                        }
                    },
                    "items": [],
                    "offer_metadata": {"a": {}},
                    "product_preferences": {"mælk": {}},
                    "recovery_code_hash": "private-recovery-hash",
                    "integrations": {
                        "samsung_food": {
                            "status": "connected",
                            "last_successful_sync": 123456,
                            "private": "do-not-copy",
                        }
                    },
                }
            },
            "invites": {"invite-secret": {"household_id": household_id}},
        },
    )

    summary = control_center._summarize_households()
    encoded = json.dumps(summary, ensure_ascii=False)
    assert summary["households"] == 1
    assert summary["members"] == 2
    assert summary["records"][0]["name"] == "Familien Test"
    assert summary["records"][0]["samsung_status"] == "connected"
    assert secret_hash not in encoded
    assert "private-member-id" not in encoded
    assert "private-recovery-hash" not in encoded
    assert "invite-secret" not in encoded
    assert household_id not in encoded


def test_publication_coverage_uses_exact_source_generation(monkeypatch):
    publication_id = "netto-35"
    current_fingerprint = "source-current"
    old_fingerprint = "source-old"
    source = {
        "publications": {
            publication_id: {
                "publication_id": publication_id,
                "retailer": "Netto",
                "title": "Netto uge 35",
                "valid_from": "20.08.2026",
                "valid_until": "26.08.2026",
                "fingerprint": current_fingerprint,
                "page_fingerprints": {"1": "a", "2": "b", "3": "c", "4": "d"},
                "status": "ready",
                "detected_at": 100,
                "ready_at": 110,
            }
        }
    }
    monkeypatch.setattr(control_center.flyer_readiness, "load_store", lambda: source)
    monkeypatch.setattr(control_center.luna_resilient_worker, "_load_quarantine", lambda: {})
    monkeypatch.setattr(control_center.luna_resilient_strong_worker, "_load_retry_state", lambda: {})

    monkeypatch.setattr(
        control_center.luna_member_coverage,
        "_load",
        lambda: {
            "items": {
                luna_member_coverage.coverage_key(publication_id, old_fingerprint): {
                    "publication_id": publication_id,
                    "fingerprint": old_fingerprint,
                    "status": "complete",
                    "pages_remaining": 0,
                }
            }
        },
    )
    rows, counts = control_center._current_publications({})
    assert rows[0]["coverage_status"] == "not_tracked"
    assert rows[0]["progress"] == 0
    assert counts["not_tracked"] == 1

    monkeypatch.setattr(
        control_center.luna_member_coverage,
        "_load",
        lambda: {
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
        },
    )
    rows, counts = control_center._current_publications(
        {publication_id: {"records": 7, "failed": 1, "member_prices": 3}}
    )
    assert rows[0]["coverage_status"] == "pending"
    assert rows[0]["pages_done"] == 3
    assert rows[0]["progress"] == 75
    assert rows[0]["member_fallback_remaining"] == 2
    assert rows[0]["member_prices_verified"] == 3
    assert counts["pending"] == 1


def test_component_state_reports_budget_and_degraded_coverage():
    runtime = {
        "core-api": {"health": "healthy", "state": "online", "latency_ms": 5, "payload": {"samsung_auth": "ok"}},
        "mobile-api": {"health": "healthy", "state": "online", "latency_ms": 4, "payload": {}},
        "samsung-login-broker": {"health": "healthy", "state": "online", "latency_ms": 3, "payload": {}},
        "luna-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {}},
        "flyer-push-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {"last_provider_check_at": 9999999999}},
        "shopping-cleanup-worker": {"health": "healthy", "state": "running", "detail": "active", "payload": {}},
    }
    states = control_center._derive_component_states(
        runtime,
        luna={
            "enabled": True,
            "api_key_configured": True,
            "usage": {"estimated_cost_dkk": 25.0, "budget_dkk": 100.0, "remaining_dkk": 75.0},
        },
        flyer_push_store={"devices": {"phone": {"enabled": True}}},
        household_summary={"households": 1},
        current_coverage={"pending": 1, "complete": 4, "degraded": 2},
    )
    by_id = {row["id"]: row for row in states}
    assert by_id["openai-luna"]["health"] == "healthy"
    assert by_id["openai-luna"]["state"] == "available"
    assert by_id["samsung-food"]["health"] == "healthy"
    assert by_id["member-coverage"]["health"] == "attention"
    assert by_id["apple-apns"]["state"] == "configured"
    assert by_id["geofence-engine"]["state"] == "deployed"


def test_control_center_static_shell_is_light_and_local():
    client = TestClient(control_center.app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["local_only"] is True

    page = client.get("/")
    assert page.status_code == 200
    assert "Kurv Control Center" in page.text
    assert 'name="color-scheme" content="light"' in page.text
    assert "dark" not in page.text.casefold()


def test_telemetry_heartbeat_roundtrip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("KURV_TELEMETRY_DIR", str(tmp_path / "heartbeats"))
    control_telemetry.write_heartbeat(
        "luna-worker",
        status="running",
        detail="Netto uge 35",
        metrics={"pending": 3},
    )
    row = control_telemetry.read_heartbeat("luna-worker", stale_after=60)
    assert row["status"] == "running"
    assert row["detail"] == "Netto uge 35"
    assert row["metrics"] == {"pending": 3}
    assert row["stale"] is False
    assert control_telemetry.all_heartbeats()["luna-worker"]["status"] == "running"

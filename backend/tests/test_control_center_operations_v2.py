from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import control_center
from app import control_center_alerts
from app import control_center_ops as ops
from app import control_center_snapshot_v2
from app import luna_controlled_worker


def _isolate_ops(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "control-center"
    monkeypatch.setattr(ops, "OPS_DIR", root)
    monkeypatch.setattr(ops, "EVENTS_PATH", root / "events.jsonl")
    monkeypatch.setattr(ops, "HISTORY_PATH", root / "history.jsonl")
    monkeypatch.setattr(ops, "STATE_PATH", root / "operations-state.json")
    monkeypatch.setattr(ops, "ALERTS_PATH", root / "alert-lifecycle.json")
    monkeypatch.setattr(ops, "BACKUP_STATUS_PATH", root / "backup-status.json")


def test_operations_assets_are_injected_into_light_shell():
    client = TestClient(control_center.app)
    response = client.get("/")
    assert response.status_code == 200
    assert '/assets/operations.css' in response.text
    assert '/assets/operations_guard.js' in response.text
    assert '/assets/operations.js' in response.text
    assert response.text.index('/assets/operations_guard.js') < response.text.index('/assets/operations.js')
    assert 'name="color-scheme" content="light"' in response.text


def test_storage_status_never_labels_whole_qnap_used_bytes_as_kurv(monkeypatch):
    monkeypatch.setattr(ops, "_dir_size", lambda path: 123_456 if str(path) == "/data" else 4_096)
    fake = SimpleNamespace(f_frsize=4096, f_blocks=1_000_000, f_bavail=250_000)
    monkeypatch.setattr(ops.os, "statvfs", lambda path: fake)

    result = ops.storage_status()

    assert result["kurv_persistent_bytes"] == 123_456
    assert result["qnap_volume_total_bytes"] == 4_096_000_000
    assert result["qnap_volume_free_bytes"] == 1_024_000_000
    assert result["qnap_volume_used_bytes"] == 3_072_000_000
    assert result["qnap_volume_used_bytes"] != result["kurv_persistent_bytes"]
    assert "hele det underliggende volume" in result["scope_note"]


def test_activity_drops_runtime_polling_but_keeps_openai_cost_and_coverage():
    result = control_center_snapshot_v2._meaningful_activity(
        legacy=[
            {"at": 10, "type": "runtime", "status": "online", "detail": "mobile-api"},
            {"at": 9, "type": "coverage", "status": "degraded", "retailer": "Lidl", "detail": "Lidl uge 34"},
        ],
        native_events=[
            {"at": 11, "category": "luna", "type": "openai_usage", "severity": "cost", "title": "OpenAI · 1 request", "detail": "Lidl", "requests": 1, "cost_dkk": 0.011},
        ],
    )

    assert [row["type"] for row in result] == ["openai_usage", "coverage"]
    assert all(row["type"] != "runtime" for row in result)
    assert result[0]["cost_dkk"] == 0.011


def test_openai_event_is_only_written_when_usage_really_increases(monkeypatch):
    captured = []
    monkeypatch.setattr(luna_controlled_worker, "append_event", lambda **kwargs: captured.append(kwargs))
    before = {"usage": {"requests": 100, "estimated_cost_dkk": 2.0, "input_tokens": 1000, "output_tokens": 100}}
    unchanged = {"usage": {"requests": 100, "estimated_cost_dkk": 2.0, "input_tokens": 1000, "output_tokens": 100}}
    after = {"usage": {"requests": 102, "estimated_cost_dkk": 2.0234, "input_tokens": 1200, "output_tokens": 140}}
    result = {"status": "enrichment-progress", "coverage_focus": {"publication_id": "p1", "retailer": "Netto", "title": "Netto uge 35"}}

    luna_controlled_worker._record_openai_event(before, unchanged, result)
    assert captured == []

    luna_controlled_worker._record_openai_event(before, after, result)
    assert len(captured) == 1
    assert captured[0]["requests"] == 2
    assert captured[0]["cost_dkk"] == pytest.approx(0.0234)
    assert captured[0]["category"] == "luna"
    assert captured[0]["type"] == "openai_usage"


def test_alert_lifecycle_counts_episodes_not_dashboard_refreshes(monkeypatch, tmp_path):
    _isolate_ops(monkeypatch, tmp_path)
    monkeypatch.setattr(control_center_alerts.ops, "ALERTS_PATH", ops.ALERTS_PATH)
    monkeypatch.setattr(control_center_alerts.ops, "EVENTS_PATH", ops.EVENTS_PATH)
    monkeypatch.setattr(control_center_alerts.time, "time", lambda: 1000)
    alert = [{"severity": "warning", "title": "14 degraded", "detail": "Quality"}]

    first = control_center_alerts.reconcile_alerts(alert)
    second = control_center_alerts.reconcile_alerts(alert)
    assert first[0]["occurrences"] == 1
    assert second[0]["occurrences"] == 1

    monkeypatch.setattr(control_center_alerts.time, "time", lambda: 1100)
    assert control_center_alerts.reconcile_alerts([]) == []
    third = control_center_alerts.reconcile_alerts(alert)
    assert third[0]["occurrences"] == 2


def test_degraded_impact_is_conservative_and_reason_based():
    result = ops.degraded_impact([
        {"publication_id": "a", "coverage_status": "degraded", "quarantine_reasons": {"member-price-ambiguous": 2}},
        {"publication_id": "b", "coverage_status": "degraded", "quarantine_reasons": {"unit-price-conflict": 1}},
        {"publication_id": "c", "coverage_status": "degraded", "quarantine_reasons": {"visual-timeout": 3}},
        {"publication_id": "d", "coverage_status": "complete", "quarantine_reasons": {"member-price-ambiguous": 99}},
    ])
    assert result["degraded_publications"] == 3
    assert result["potential_member_price_publications"] == 1
    assert result["potential_price_publications"] == 1
    assert result["customer_sensitive_publications"] == 2
    assert result["other_quality_publications"] == 1
    assert "ikke det samme" in result["note"]


def test_client_fleet_never_exposes_device_tokens():
    token = "secret-device-token"
    result = ops.client_fleet({"devices": {token: {"enabled": True, "environment": "sandbox", "app_build": "61", "token": token}}})
    encoded = json.dumps(result)
    assert result["registered"] == 1
    assert result["enabled"] == 1
    assert token not in encoded
    assert result["clients"][0]["build"] == "61"


def test_history_sampling_is_bounded_and_does_not_write_business_state(monkeypatch, tmp_path):
    _isolate_ops(monkeypatch, tmp_path)
    monkeypatch.setattr(ops.time, "time", lambda: 10_000)
    snapshot = {
        "generated_at": 10_000,
        "runtime": {"core-api": {"state": "online", "health": "healthy", "latency_ms": 10}},
        "luna": {"usage": {"requests": 5, "estimated_cost_dkk": 0.1}, "current_coverage": {"complete": 1, "pending": 0, "degraded": 0}, "quarantined": 0, "retry_candidates": 0},
        "flyers": {"publications": []},
        "overall": {"status": "healthy"},
    }
    ops.record_snapshot(snapshot)
    rows = ops.history(hours=168)
    assert len(rows) == 1
    assert rows[0]["luna_requests"] == 5
    assert not (tmp_path / "households.json").exists()

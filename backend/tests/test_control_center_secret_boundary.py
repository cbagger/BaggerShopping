from __future__ import annotations

import json
from pathlib import Path

from app import luna_controlled_worker


def test_control_center_compose_service_has_no_env_file_or_secret_mounts():
    text = Path("docker-compose.yml").read_text("utf-8")
    section = text.split("\n  control-center:\n", 1)[1].split("\n  login-ui:\n", 1)[0]

    assert "env_file:" not in section
    assert ".env" not in section
    assert "./secrets" not in section
    assert "/run/secrets" not in section
    assert "docker.sock" not in section
    assert '"8092:8092"' in section


def test_luna_heartbeat_exposes_operational_facts_not_credentials(monkeypatch):
    openai_secret = "sk-super-secret-value"
    samsung_secret = "samsung-super-secret-value"
    apns_secret = "apns-super-secret-value"

    monkeypatch.setattr(
        luna_controlled_worker,
        "luna_status",
        lambda: {
            "enabled": True,
            "apply_results": True,
            "model": "gpt-5.6-luna",
            "api_key_configured": True,
            "usage": {"requests": 42, "estimated_cost_dkk": 1.23},
            "records": {"completed": 5},
            "OPENAI_API_KEY": openai_secret,
            "SAMSUNG_PASSWORD": samsung_secret,
            "APNS_PRIVATE_KEY": apns_secret,
        },
    )
    monkeypatch.setattr(
        luna_controlled_worker.luna_member_coverage,
        "status_payload",
        lambda: {"counts": {"pending": 1, "complete": 2, "degraded": 0}},
    )
    monkeypatch.setattr(luna_controlled_worker, "_load_quarantine", lambda: {})
    monkeypatch.setattr(luna_controlled_worker, "_LAST_RESULT", {"status": "enrichment-progress"})

    status, detail, metrics = luna_controlled_worker._heartbeat_payload()
    encoded = json.dumps(metrics, ensure_ascii=False)

    assert status == "running"
    assert metrics["api_key_configured"] is True
    assert metrics["model"] == "gpt-5.6-luna"
    assert metrics["enabled"] is True
    assert metrics["usage"]["requests"] == 42
    assert openai_secret not in encoded
    assert samsung_secret not in encoded
    assert apns_secret not in encoded
    assert "OPENAI_API_KEY" not in metrics
    assert "SAMSUNG_PASSWORD" not in metrics
    assert "APNS_PRIVATE_KEY" not in metrics

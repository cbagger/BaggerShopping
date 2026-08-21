from __future__ import annotations

import asyncio

from app import control_center


def test_mobile_docs_404_is_expected_internal_liveness(monkeypatch):
    async def fake_runtime_probes(*, force: bool = False):
        return {
            "core-api": {
                "name": "core-api",
                "ok": True,
                "health": "healthy",
                "state": "online",
                "status_code": 200,
                "error": None,
            },
            "mobile-api": {
                "name": "mobile-api",
                "ok": False,
                "health": "error",
                "state": "unavailable",
                "status_code": 404,
                "latency_ms": 12,
                "error": "HTTP 404",
            },
        }

    monkeypatch.setattr(control_center, "_base_runtime_probes", fake_runtime_probes)
    probes = asyncio.run(control_center._safe_runtime_probes(force=True))
    mobile = probes["mobile-api"]

    assert mobile["ok"] is True
    assert mobile["health"] == "healthy"
    assert mobile["state"] == "online"
    assert mobile["status_code"] == 404
    assert mobile["error"] is None
    assert mobile["liveness_evidence"] == "expected-disabled-docs-404"


def test_mobile_non_404_probe_failure_remains_failure(monkeypatch):
    async def fake_runtime_probes(*, force: bool = False):
        return {
            "mobile-api": {
                "name": "mobile-api",
                "ok": False,
                "health": "error",
                "state": "unavailable",
                "status_code": 503,
                "error": "HTTP 503",
            }
        }

    monkeypatch.setattr(control_center, "_base_runtime_probes", fake_runtime_probes)
    probes = asyncio.run(control_center._safe_runtime_probes(force=True))
    mobile = probes["mobile-api"]

    assert mobile["ok"] is False
    assert mobile["health"] == "error"
    assert mobile["state"] == "unavailable"
    assert mobile["status_code"] == 503
    assert mobile["error"] == "HTTP 503"


def test_control_center_dashboard_version_is_1_3_0():
    assert control_center.APP_VERSION == "1.4.0"

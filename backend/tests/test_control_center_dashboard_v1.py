from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import control_center


STATIC = Path(control_center.STATIC_DIR)


def test_dashboard_assets_are_injected_after_operations_assets():
    response = TestClient(control_center.app).get("/")
    assert response.status_code == 200
    assert '/assets/operations.css' in response.text
    assert '/assets/dashboard.css' in response.text
    assert '/assets/operations.js' in response.text
    assert '/assets/dashboard.js' in response.text
    assert response.text.index('/assets/operations.css') < response.text.index('/assets/dashboard.css')
    assert response.text.index('/assets/operations.js') < response.text.index('/assets/dashboard.js')


def test_dashboard_script_builds_real_pages_and_executive_summary():
    source = (STATIC / "dashboard.js").read_text("utf-8")
    assert 'overview: "Dashboard"' in source
    assert 'operations: "Drift & system"' in source
    assert 'id = "operations"' in source
    assert 'dashboardSnapshotGrid' in source
    assert 'dashboardSystem' in source
    assert 'dashboardLuna' in source
    assert 'dashboardFlyers' in source
    assert 'dashboardOpenAI' in source
    assert 'dashboardStorage' in source
    assert 'dashboardDeploy' in source
    assert 'page-navigation-ready' in source
    assert 'MutationObserver' not in source


def test_openai_history_includes_full_date_and_clock_time():
    source = (STATIC / "dashboard.js").read_text("utf-8")
    assert 'year: "numeric"' in source
    assert 'hour: "2-digit"' in source
    assert 'minute: "2-digit"' in source
    assert 'dashboard-openai-event-main' in source
    assert '<time datetime=' in source
    assert 'cost_dkk' in source


def test_mobile_navigation_is_fixed_left_rail_not_bottom_bar():
    css = (STATIC / "dashboard.css").read_text("utf-8")
    assert '@media (max-width: 820px)' in css
    assert 'inset: 0 auto 0 0' in css
    assert 'height: 100vh' in css
    assert '--mobile-rail-width' in css
    assert 'margin-left: var(--mobile-rail-width)' in css


def test_health_advertises_paged_dashboard():
    response = TestClient(control_center.app).get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.2.0"
    assert payload["paged_dashboard"] is True
    assert payload["read_only"] is True

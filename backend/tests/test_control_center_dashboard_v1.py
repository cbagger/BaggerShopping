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


def test_dashboard_uses_static_pages_and_one_executive_summary():
    html = (STATIC / "index.html").read_text("utf-8")
    source = (STATIC / "dashboard.js").read_text("utf-8")
    assert 'overview: "Dashboard"' in source
    assert 'operations: "Drift & system"' in source
    assert 'id="operations"' in html
    assert 'id="dashboardSnapshotGrid"' in html
    assert 'dashboardSystem' in source
    assert 'dashboardLuna' in source
    assert 'dashboardFlyers' in source
    assert 'dashboardMembers' in source
    assert 'deploymentAction' in source
    assert 'page-navigation-ready' in source
    assert 'ensureDashboardCards' not in source
    assert 'MutationObserver' not in source


def test_dashboard_uses_real_kurv_brand_asset_and_no_legacy_metric_grid():
    html = (STATIC / "index.html").read_text("utf-8")
    css = (STATIC / "dashboard.css").read_text("utf-8")
    assert (STATIC / "kurv-app-icon.png").is_file()
    assert html.count('/assets/kurv-app-icon.png') >= 2
    assert 'id="metricGrid"' not in html
    assert '.metric-grid' in css
    assert 'display: none !important' in css


def test_extensions_share_the_base_snapshot_stream():
    app_source = (STATIC / "app.js").read_text("utf-8")
    dashboard_source = (STATIC / "dashboard.js").read_text("utf-8")
    operations_source = (STATIC / "operations.js").read_text("utf-8")
    assert 'new CustomEvent("kurv:snapshot"' in app_source
    assert 'window.addEventListener("kurv:snapshot"' in dashboard_source
    assert 'window.addEventListener("kurv:snapshot"' in operations_source
    assert 'fetch("/api/snapshot"' not in dashboard_source
    assert 'fetch("/api/snapshot"' not in operations_source
    assert 'new EventSource' not in dashboard_source
    assert 'new EventSource' not in operations_source


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
    assert payload["version"] == "1.4.0"
    assert payload["paged_dashboard"] is True
    assert payload["read_only"] is True

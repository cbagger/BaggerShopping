from __future__ import annotations

import time
from collections import Counter
from typing import Any

from . import control_center_ops as ops
from . import control_center_snapshot as base


async def build_snapshot(*, control_center_version: str) -> dict[str, Any]:
    """Add operations intelligence without changing Kurv business state."""
    snapshot = await base.build_snapshot(control_center_version=control_center_version)

    runtime = snapshot.get("runtime", {}) if isinstance(snapshot.get("runtime"), dict) else {}
    luna = snapshot.get("luna", {}) if isinstance(snapshot.get("luna"), dict) else {}
    flyers = snapshot.get("flyers", {}) if isinstance(snapshot.get("flyers"), dict) else {}
    publications = flyers.get("publications", []) if isinstance(flyers.get("publications"), list) else []
    current_coverage = luna.get("current_coverage", {}) if isinstance(luna.get("current_coverage"), dict) else {}
    households = snapshot.get("data", {}).get("households", {}) if isinstance(snapshot.get("data"), dict) else {}
    integrations = snapshot.get("integrations", {}) if isinstance(snapshot.get("integrations"), dict) else {}
    samsung = integrations.get("samsung", {}) if isinstance(integrations.get("samsung"), dict) else {}

    push_store = base.flyer_push._load()
    _, luna_events = base.luna_store_summary()
    freshness = ops.freshness_status(
        runtime=runtime,
        publications=publications,
        flyer_push_store=push_store,
        samsung=samsung,
        luna_events=luna_events,
    )
    jobs = ops.job_status(runtime)
    degraded = ops.degraded_impact(publications)
    clients = ops.client_fleet(push_store)
    security = ops.security_posture(luna=luna, samsung=samsung, flyer_push_store=push_store, runtime=runtime)
    end_to_end = ops.end_to_end_status(
        runtime=runtime,
        freshness=freshness,
        current_coverage=current_coverage,
        household_summary=households,
        samsung=samsung,
    )
    release = snapshot.get("release", {}) if isinstance(snapshot.get("release"), dict) else {}
    deployment = ops.deployment_status(release)
    backup = ops.backup_status()
    storage = ops.storage_status()
    data_integrity = _data_integrity(snapshot)
    integration_quality = _integration_quality(snapshot, freshness=freshness, clients=clients)
    dependency_map = _dependency_map(snapshot)

    # Alert lifecycle is operational metadata only. It never acknowledges,
    # suppresses or changes production alarms; it adds first/last seen context.
    active_alerts = ops.reconcile_alerts(snapshot.get("alerts", []))
    snapshot["alerts"] = active_alerts
    if isinstance(snapshot.get("overall"), dict):
        snapshot["overall"]["alerts"] = len(active_alerts)

    if isinstance(snapshot.get("data"), dict):
        snapshot["data"]["storage"] = storage
        # Keep the legacy key for old frontend code, but replace its semantics so
        # it can no longer pretend whole-QNAP used bytes belong to Kurv.
        snapshot["data"]["volume"] = {
            "total_bytes": None,
            "used_bytes": storage.get("kurv_persistent_bytes"),
            "free_bytes": storage.get("qnap_volume_free_bytes"),
            "used_percent": None,
            "scope": "kurv-persistent-plus-separate-qnap-free",
        }
        snapshot["data"]["integrity"] = data_integrity

    snapshot["operations"] = {
        "end_to_end": end_to_end,
        "freshness": freshness,
        "jobs": jobs,
        "degraded_impact": degraded,
        "integration_quality": integration_quality,
        "deployment": deployment,
        "backup": backup,
        "security": security,
        "clients": clients,
        "dependency_map": dependency_map,
    }

    # Persist only low-frequency observability history and state transitions.
    # This is independent of business/persistent shopping state.
    ops.record_snapshot(snapshot)
    history = ops.history(hours=168)
    snapshot["operations"]["trends"] = ops.trends_summary(history)

    meaningful = _meaningful_activity(
        legacy=snapshot.get("telemetry", {}).get("timeline", []) if isinstance(snapshot.get("telemetry"), dict) else [],
        native_events=ops.events(limit=180),
    )
    if isinstance(snapshot.get("telemetry"), dict):
        snapshot["telemetry"]["timeline"] = meaningful
        snapshot["telemetry"]["activity_categories"] = _activity_counts(meaningful)

    return snapshot


def _meaningful_activity(*, legacy: list[dict[str, Any]], native_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in native_events:
        if not isinstance(row, dict):
            continue
        rows.append({**row, "status": row.get("severity") or row.get("status")})

    # Preserve actual Luna/coverage history from v1 but deliberately drop
    # per-refresh runtime polling. Runtime belongs in Live Runtime, not Activity.
    for row in legacy:
        if not isinstance(row, dict) or row.get("type") == "runtime":
            continue
        event_type = str(row.get("type") or "event")
        category = "flyer" if event_type == "coverage" else "luna"
        rows.append({
            "at": row.get("at"),
            "category": category,
            "type": event_type,
            "severity": "warning" if row.get("status") == "degraded" else "success" if row.get("status") == "complete" else "info",
            "status": row.get("status"),
            "title": row.get("detail") or event_type,
            "detail": row.get("retailer") or None,
            "retailer": row.get("retailer") or None,
            "requests": None,
            "cost_dkk": None,
        })

    seen = set()
    result = []
    for row in sorted(rows, key=lambda value: int(value.get("at") or 0), reverse=True):
        key = (row.get("at"), row.get("type"), row.get("title"), row.get("detail"), row.get("requests"), row.get("cost_dkk"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= 120:
            break
    return result


def _activity_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("category") or "other") for row in rows)
    return dict(counts)


def _data_integrity(snapshot: dict[str, Any]) -> dict[str, Any]:
    data = snapshot.get("data", {}) if isinstance(snapshot.get("data"), dict) else {}
    flyers = snapshot.get("flyers", {}) if isinstance(snapshot.get("flyers"), dict) else {}
    publications = flyers.get("publications", []) if isinstance(flyers.get("publications"), list) else []
    households = data.get("households", {}) if isinstance(data.get("households"), dict) else {}
    metadata = data.get("offer_metadata", {}) if isinstance(data.get("offer_metadata"), dict) else {}

    checks = [
        {"name": "Familie-store", "status": "healthy" if households.get("households", 0) > 0 else "attention", "detail": f"{households.get('households', 0)} familier · {households.get('members', 0)} medlemmer"},
        {"name": "Offer metadata", "status": "healthy", "detail": f"{metadata.get('records', 0)} records · {metadata.get('pinned', 0)} pinned"},
        {"name": "Aktuelle source fingerprints", "status": "healthy" if all(row.get("fingerprint") for row in publications) else "attention", "detail": f"{sum(1 for row in publications if row.get('fingerprint'))}/{len(publications)} aktuelle generationer fingerprinted"},
        {"name": "Coverage uden pending", "status": "healthy" if snapshot.get("luna", {}).get("current_coverage", {}).get("pending", 0) == 0 else "attention", "detail": f"{snapshot.get('luna', {}).get('current_coverage', {}).get('pending', 0)} pending"},
    ]
    attention = sum(1 for row in checks if row["status"] != "healthy")
    return {"status": "healthy" if attention == 0 else "attention", "checks": checks, "attention": attention}


def _integration_quality(snapshot: dict[str, Any], *, freshness: list[dict[str, Any]], clients: dict[str, Any]) -> list[dict[str, Any]]:
    by_freshness = {row["name"]: row for row in freshness}
    runtime = snapshot.get("runtime", {})
    samsung = snapshot.get("integrations", {}).get("samsung", {})
    samsung_payload = samsung.get("payload", {}) if isinstance(samsung.get("payload"), dict) else {}
    luna = snapshot.get("luna", {})
    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    push = snapshot.get("flyers", {}).get("push", {})
    return [
        {
            "id": "samsung",
            "name": "Samsung Food",
            "health": "healthy" if samsung_payload.get("samsung_auth") == "ok" else "attention",
            "state": samsung_payload.get("samsung_auth") or "unknown",
            "latency_ms": samsung.get("latency_ms"),
            "last_success_at": samsung.get("checked_at"),
            "detail": "Tokenvalidering caches i 5 min.; realtime dashboard udløser ikke Samsung-kald.",
        },
        {
            "id": "provider",
            "name": "Tjek / eTilbudsavis",
            "health": by_freshness.get("Provider check", {}).get("health", "unknown"),
            "state": "active" if by_freshness.get("Provider check", {}).get("health") == "healthy" else "stale",
            "latency_ms": None,
            "last_success_at": by_freshness.get("Provider check", {}).get("at"),
            "detail": "Provider discovery fra flyer-push worker.",
        },
        {
            "id": "openai",
            "name": "OpenAI Luna",
            "health": "healthy" if luna.get("api_key_configured") else "attention",
            "state": "available" if luna.get("api_key_configured") else "missing-key",
            "latency_ms": None,
            "last_success_at": by_freshness.get("Luna event", {}).get("at"),
            "detail": f"{usage.get('requests', 0)} requests · {usage.get('estimated_cost_dkk', 0):.2f}/{usage.get('budget_dkk', 0):.0f} kr.",
        },
        {
            "id": "apns",
            "name": "Apple Push Notifications",
            "health": "healthy" if clients.get("enabled", 0) > 0 and runtime.get("flyer-push-worker", {}).get("health") == "healthy" else "attention",
            "state": "configured" if clients.get("enabled", 0) > 0 else "no-devices",
            "latency_ms": None,
            "last_success_at": push.get("last_ready_delivery_at"),
            "detail": f"{clients.get('enabled', 0)} aktive af {clients.get('registered', 0)} registrerede klienter.",
        },
    ]


def _dependency_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    components = {row.get("id"): row for row in snapshot.get("components", []) if isinstance(row, dict)}
    edges = []
    for edge in snapshot.get("dataflow", []):
        if not isinstance(edge, dict):
            continue
        source = components.get(edge.get("from"), {})
        target = components.get(edge.get("to"), {})
        healths = {source.get("health"), target.get("health")}
        health = "error" if "error" in healths else "attention" if "attention" in healths else "healthy"
        edges.append({**edge, "health": health, "source_state": source.get("state"), "target_state": target.get("state")})
    return {"edges": edges, "generated_at": int(time.time())}

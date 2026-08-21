from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


OPS_DIR = Path(os.getenv("KURV_CONTROL_CENTER_OPS_DIR", "/data/control-center"))
EVENTS_PATH = OPS_DIR / "events.jsonl"
HISTORY_PATH = OPS_DIR / "history.jsonl"
STATE_PATH = OPS_DIR / "operations-state.json"
ALERTS_PATH = OPS_DIR / "alert-lifecycle.json"
BACKUP_STATUS_PATH = OPS_DIR / "backup-status.json"
HISTORY_SAMPLE_SECONDS = 300
MAX_EVENTS = 1200
MAX_HISTORY = 7 * 24 * 12 + 24


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return value


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
        temporary.replace(path)
    except Exception:
        return


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _append_jsonl(path: Path, payload: dict[str, Any], *, keep: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        # Compact opportunistically. Observability may never break Kurv.
        if path.stat().st_size > 2_500_000:
            rows = _read_jsonl(path, limit=keep)
            temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
            temporary.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), "utf-8")
            temporary.replace(path)
    except Exception:
        return


def append_event(
    *,
    category: str,
    event_type: str,
    title: str,
    detail: str | None = None,
    severity: str = "info",
    component: str | None = None,
    retailer: str | None = None,
    publication_id: str | None = None,
    requests: int | None = None,
    cost_dkk: float | None = None,
    metadata: dict[str, Any] | None = None,
    at: int | None = None,
) -> None:
    row = {
        "at": int(at or time.time()),
        "category": str(category),
        "type": str(event_type),
        "severity": str(severity),
        "title": str(title)[:220],
        "detail": str(detail)[:500] if detail else None,
        "component": component,
        "retailer": retailer,
        "publication_id": publication_id,
        "requests": int(requests) if requests is not None else None,
        "cost_dkk": round(float(cost_dkk), 6) if cost_dkk is not None else None,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
    _append_jsonl(EVENTS_PATH, row, keep=MAX_EVENTS)


def events(*, limit: int = 160) -> list[dict[str, Any]]:
    rows = _read_jsonl(EVENTS_PATH, limit=max(1, min(limit, MAX_EVENTS)))
    rows.sort(key=lambda row: int(row.get("at") or 0), reverse=True)
    return rows


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def storage_status() -> dict[str, Any]:
    """Separate Kurv's own footprint from the shared QNAP volume.

    `/data` is Kurv persistent state. statvfs describes the QNAP volume backing
    that mount, so host capacity is explicitly labelled host-wide and is never
    presented as Kurv's own used bytes.
    """
    persistent = Path("/data")
    kurv_bytes = _dir_size(persistent)
    ops_bytes = _dir_size(OPS_DIR)
    try:
        stats = os.statvfs(str(persistent))
        total = stats.f_frsize * stats.f_blocks
        free = stats.f_frsize * stats.f_bavail
        host_used = max(0, total - free)
    except OSError:
        total = free = host_used = None
    flyer_files = {
        "readiness": persistent / "flyer-readiness.json",
        "serving_cache": persistent / "flyer-serving-cache.json",
        "luna_analysis": persistent / "luna-enrichment-store.json",
        "coverage": persistent / "luna-member-coverage.json",
        "quality_filter": persistent / "luna-quarantined-work.json",
        "retry_state": persistent / "luna-retry-work.json",
    }
    flyer_breakdown: dict[str, int] = {}
    for name, path in flyer_files.items():
        try:
            flyer_breakdown[name] = path.stat().st_size
        except OSError:
            flyer_breakdown[name] = 0
    flyer_bytes = sum(flyer_breakdown.values())
    return {
        "kurv_persistent_bytes": kurv_bytes,
        "flyer_history_bytes": flyer_bytes,
        "flyer_history_breakdown": flyer_breakdown,
        "other_kurv_bytes": max(0, kurv_bytes - flyer_bytes),
        "control_center_telemetry_bytes": ops_bytes,
        "qnap_volume_total_bytes": total,
        "qnap_volume_free_bytes": free,
        "qnap_volume_used_bytes": host_used,
        "qnap_volume_used_percent": round(host_used / total * 100, 1) if total else None,
        "scope_note": "Kurv-forbrug er kun /data. Avisarkivet er JSON-data og URL-referencer; selve avisbillederne gemmes ikke lokalt. QNAP-tal er hele det underliggende volume og vises separat.",
    }


def _freshness_item(name: str, timestamp: Any, *, healthy_seconds: int, warning_seconds: int) -> dict[str, Any]:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    age = max(0, int(time.time()) - value) if value else None
    if age is None:
        health = "unknown"
    elif age <= healthy_seconds:
        health = "healthy"
    elif age <= warning_seconds:
        health = "attention"
    else:
        health = "stale"
    return {"name": name, "at": value or None, "age_seconds": age, "health": health}


def freshness_status(
    *,
    runtime: dict[str, dict[str, Any]],
    publications: list[dict[str, Any]],
    flyer_push_store: dict[str, Any],
    samsung: dict[str, Any],
    luna_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_publication = max((int(row.get("detected_at") or 0) for row in publications), default=0)
    latest_luna = max((int(row.get("at") or 0) for row in luna_events), default=0)
    return [
        _freshness_item("Provider check", runtime.get("flyer-push-worker", {}).get("payload", {}).get("last_provider_check_at"), healthy_seconds=1800, warning_seconds=7200),
        _freshness_item("Nyeste avisdata", latest_publication, healthy_seconds=36 * 3600, warning_seconds=72 * 3600),
        _freshness_item("Luna event", latest_luna, healthy_seconds=6 * 3600, warning_seconds=24 * 3600),
        _freshness_item("APNs levering", flyer_push_store.get("last_ready_delivery_at"), healthy_seconds=48 * 3600, warning_seconds=7 * 24 * 3600),
        _freshness_item("Samsung validering", samsung.get("checked_at"), healthy_seconds=600, warning_seconds=1800),
    ]


def job_status(runtime: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    definitions = [
        ("luna-worker", "Luna coverage"),
        ("flyer-push-worker", "Flyer push"),
        ("shopping-cleanup-worker", "Shopping cleanup"),
    ]
    for component, label in definitions:
        row = runtime.get(component, {})
        metrics = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
        result.append({
            "component": component,
            "name": label,
            "state": row.get("state"),
            "health": row.get("health"),
            "detail": row.get("detail"),
            "age_seconds": row.get("age_seconds"),
            "focus": metrics.get("focus"),
            "coverage": metrics.get("coverage"),
            "next_run_in_hours": metrics.get("next_run_in_hours"),
            "last_provider_check_at": metrics.get("last_provider_check_at"),
            "last_ready_delivery_at": metrics.get("last_ready_delivery_at"),
        })
    return result


def degraded_impact(publications: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in publications if row.get("coverage_status") == "degraded"]
    reasons: Counter[str] = Counter()
    affected_member = set()
    affected_price = set()
    affected_other = set()
    for row in rows:
        pid = str(row.get("publication_id") or "")
        row_reasons = row.get("quarantine_reasons", {}) if isinstance(row.get("quarantine_reasons"), dict) else {}
        if not row_reasons:
            affected_other.add(pid)
        for reason, count in row_reasons.items():
            label = str(reason)
            reasons[label] += int(count or 0)
            lowered = label.casefold()
            if any(token in lowered for token in ("member", "membership", "club", "plus", "loyalty")):
                affected_member.add(pid)
            elif any(token in lowered for token in ("price", "pricing", "unit", "currency", "badge", "amount")):
                affected_price.add(pid)
            else:
                affected_other.add(pid)
    customer_sensitive = affected_member | affected_price
    return {
        "degraded_publications": len(rows),
        "potential_member_price_publications": len(affected_member),
        "potential_price_publications": len(affected_price),
        "other_quality_publications": len(affected_other - customer_sensitive),
        "customer_sensitive_publications": len(customer_sensitive),
        "top_reasons": [{"reason": reason, "count": count} for reason, count in reasons.most_common(12)],
        "note": "Impact er konservativt grupperet fra quarantine-årsager; det er ikke det samme som dokumenterede forkerte kundepriser.",
    }


def client_fleet(flyer_push_store: dict[str, Any]) -> dict[str, Any]:
    devices = flyer_push_store.get("devices", {}) if isinstance(flyer_push_store.get("devices"), dict) else {}
    rows = []
    for index, value in enumerate(devices.values(), start=1):
        if not isinstance(value, dict):
            continue
        rows.append({
            "label": f"iPhone {index}",
            "enabled": bool(value.get("enabled")),
            "environment": value.get("environment") or value.get("apns_environment"),
            "registered_at": value.get("registered_at") or value.get("created_at"),
            "updated_at": value.get("updated_at") or value.get("last_seen_at"),
            "last_success_at": value.get("last_success_at") or value.get("last_delivery_at"),
            "build": value.get("build") or value.get("app_build"),
            "version": value.get("version") or value.get("app_version"),
            "geofence_permission": value.get("geofence_permission") if "geofence_permission" in value else None,
            "push_permission": value.get("push_permission") if "push_permission" in value else ("enabled" if value.get("enabled") else "disabled"),
        })
    return {
        "registered": len(rows),
        "enabled": sum(1 for row in rows if row["enabled"]),
        "clients": rows,
        "note": "Client fleet v1 bruger sanitiseret APNs-registration. Permission/build vises kun hvis klienten allerede har rapporteret feltet.",
    }


def security_posture(*, luna: dict[str, Any], samsung: dict[str, Any], flyer_push_store: dict[str, Any], runtime: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    samsung_payload = samsung.get("payload", {}) if isinstance(samsung.get("payload"), dict) else {}
    enabled_devices = sum(1 for row in flyer_push_store.get("devices", {}).values() if isinstance(row, dict) and row.get("enabled"))
    return [
        {"name": "LAN-only request gate", "status": "healthy", "detail": "Privat/loopback klient + lokal Host kræves"},
        {"name": "Control Center secrets", "status": "healthy", "detail": "Ingen production .env eller secret mounts"},
        {"name": "Docker socket", "status": "healthy", "detail": "Ikke monteret"},
        {"name": "OpenAI credential", "status": "healthy" if luna.get("api_key_configured") else "attention", "detail": "Konfigureret" if luna.get("api_key_configured") else "Mangler"},
        {"name": "Samsung auth", "status": "healthy" if samsung_payload.get("samsung_auth") == "ok" else "attention", "detail": str(samsung_payload.get("samsung_auth") or "ukendt")},
        {"name": "APNs", "status": "healthy" if enabled_devices else "attention", "detail": f"{enabled_devices} aktive push-enheder"},
        {"name": "Mobile API process", "status": runtime.get("mobile-api", {}).get("health", "unknown"), "detail": str(runtime.get("mobile-api", {}).get("liveness_evidence") or runtime.get("mobile-api", {}).get("state") or "ukendt")},
    ]


def backup_status() -> dict[str, Any]:
    payload = _read_json(BACKUP_STATUS_PATH, {})
    if not isinstance(payload, dict) or not payload:
        return {"status": "unknown", "last_backup_at": None, "age_seconds": None, "path_label": None, "verified": False, "note": "Backup-registry bliver udfyldt af næste validerede Kurv-deploy."}
    at = int(payload.get("last_backup_at") or 0)
    age = max(0, int(time.time()) - at) if at else None
    status = "healthy" if age is not None and age < 14 * 24 * 3600 and payload.get("verified") else "attention"
    return {**payload, "status": status, "age_seconds": age}


def deployment_status(release: dict[str, Any]) -> dict[str, Any]:
    build_commit = os.getenv("KURV_BUILD_COMMIT", "").strip() or "unknown"
    marker_commit = str(release.get("commit") or "unknown")
    if build_commit == "unknown" or marker_commit == "unknown":
        drift = "unknown"
    else:
        drift = "none" if build_commit == marker_commit else "detected"
    return {
        "build_commit": build_commit,
        "marker_commit": marker_commit,
        "drift": drift,
        "control_center": release.get("control_center"),
        "ios": release.get("ios"),
        "marker_updated_at": release.get("deployment_marker", {}).get("updated_at") if isinstance(release.get("deployment_marker"), dict) else None,
    }


def end_to_end_status(*, runtime: dict[str, dict[str, Any]], freshness: list[dict[str, Any]], current_coverage: dict[str, int], household_summary: dict[str, Any], samsung: dict[str, Any]) -> dict[str, Any]:
    fresh = {row["name"]: row for row in freshness}
    stages = [
        {"name": "Provider", "status": "healthy" if fresh.get("Provider check", {}).get("health") == "healthy" else "attention", "detail": "Seneste provider discovery"},
        {"name": "Core API", "status": runtime.get("core-api", {}).get("health", "unknown"), "detail": runtime.get("core-api", {}).get("state")},
        {"name": "Mobile API", "status": runtime.get("mobile-api", {}).get("health", "unknown"), "detail": runtime.get("mobile-api", {}).get("state")},
        {"name": "Luna coverage", "status": "healthy" if int(current_coverage.get("pending", 0)) == 0 else "attention", "detail": f"{current_coverage.get('pending', 0)} pending · {current_coverage.get('degraded', 0)} degraded"},
        {"name": "Familiedata", "status": "healthy" if household_summary.get("households", 0) > 0 else "attention", "detail": f"{household_summary.get('households', 0)} familier"},
        {"name": "Samsung", "status": "healthy" if samsung.get("payload", {}).get("samsung_auth") == "ok" else "attention", "detail": samsung.get("payload", {}).get("samsung_auth") or "ukendt"},
    ]
    critical = any(row["status"] == "error" for row in stages)
    attention = any(row["status"] not in {"healthy", "info"} for row in stages)
    operational = "critical" if critical else "attention" if attention else "healthy"
    quality = "filtered" if int(current_coverage.get("degraded", 0)) > 0 else "healthy"
    return {
        "operational_status": operational,
        "quality_status": quality,
        "mode": "read-only synthetic evidence",
        "stages": stages,
        "note": "Kontrollerer kunde-read-path og integrationsbeviser uden at skrive testvarer til familiens liste.",
    }


def _alert_key(row: dict[str, Any]) -> str:
    return "|".join((str(row.get("severity") or ""), str(row.get("title") or ""), str(row.get("detail") or "")))[:1200]


def reconcile_alerts(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = int(time.time())
    store = _read_json(ALERTS_PATH, {"active": {}, "resolved": []})
    if not isinstance(store, dict):
        store = {"active": {}, "resolved": []}
    previous = store.get("active", {}) if isinstance(store.get("active"), dict) else {}
    resolved = store.get("resolved", []) if isinstance(store.get("resolved"), list) else []
    next_active: dict[str, dict[str, Any]] = {}
    enriched: list[dict[str, Any]] = []
    for row in active:
        key = _alert_key(row)
        old = previous.get(key, {}) if isinstance(previous.get(key), dict) else {}
        first_seen = int(old.get("first_seen") or now)
        occurrences = int(old.get("occurrences") or 0) + 1
        life = {"first_seen": first_seen, "last_seen": now, "duration_seconds": max(0, now - first_seen), "occurrences": occurrences}
        next_active[key] = life
        enriched.append({**row, **life})
    for key, old in previous.items():
        if key in next_active or not isinstance(old, dict):
            continue
        resolved.append({"key": key, "first_seen": old.get("first_seen"), "resolved_at": now, "duration_seconds": max(0, now - int(old.get("first_seen") or now))})
        append_event(category="system", event_type="alert_resolved", title="Alarm løst", detail=key.split("|", 2)[1] if "|" in key else key, severity="success", at=now)
    _write_json(ALERTS_PATH, {"active": next_active, "resolved": resolved[-80:]})
    return enriched


def _history_metric(snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = snapshot.get("runtime", {})
    luna = snapshot.get("luna", {})
    coverage = luna.get("current_coverage", {}) if isinstance(luna.get("current_coverage"), dict) else {}
    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    return {
        "at": int(snapshot.get("generated_at") or time.time()),
        "core_ms": runtime.get("core-api", {}).get("latency_ms"),
        "mobile_ms": runtime.get("mobile-api", {}).get("latency_ms"),
        "luna_requests": usage.get("requests"),
        "luna_cost_dkk": usage.get("estimated_cost_dkk"),
        "coverage_complete": coverage.get("complete"),
        "coverage_pending": coverage.get("pending"),
        "coverage_degraded": coverage.get("degraded"),
        "quarantined": luna.get("quarantined"),
        "retries": luna.get("retry_candidates"),
        "overall": snapshot.get("overall", {}).get("status"),
    }


def _record_transition_events(snapshot: dict[str, Any], previous: dict[str, Any]) -> None:
    runtime_now = snapshot.get("runtime", {})
    runtime_before = previous.get("runtime", {}) if isinstance(previous.get("runtime"), dict) else {}
    for name, row in runtime_now.items():
        state = str(row.get("state") or "unknown")
        old_state = str(runtime_before.get(name, {}).get("state") or "unknown") if isinstance(runtime_before.get(name), dict) else "unknown"
        if state != old_state and old_state != "unknown":
            severity = "error" if row.get("health") == "error" else "warning" if row.get("health") == "attention" else "success"
            append_event(category="system", event_type="runtime_transition", title=f"{name}: {old_state} → {state}", detail=row.get("detail") or row.get("error"), severity=severity, component=name)

    before_cov = previous.get("coverage", {}) if isinstance(previous.get("coverage"), dict) else {}
    for row in snapshot.get("flyers", {}).get("publications", []):
        pid = str(row.get("publication_id") or "")
        status = str(row.get("coverage_status") or "unknown")
        old = str(before_cov.get(pid) or "unknown")
        if old != "unknown" and status != old:
            append_event(category="flyer", event_type="coverage_transition", title=f"{row.get('retailer')} · {row.get('title')}", detail=f"Coverage {old} → {status}", severity="warning" if status == "degraded" else "success", retailer=row.get("retailer"), publication_id=pid)


def record_snapshot(snapshot: dict[str, Any]) -> None:
    """Persist low-frequency trends and transitions, never business state."""
    try:
        state = _read_json(STATE_PATH, {})
        if not isinstance(state, dict):
            state = {}
        _record_transition_events(snapshot, state)
        now = int(snapshot.get("generated_at") or time.time())
        last_sample = int(state.get("last_sample_at") or 0)
        if now - last_sample >= HISTORY_SAMPLE_SECONDS:
            _append_jsonl(HISTORY_PATH, _history_metric(snapshot), keep=MAX_HISTORY)
            last_sample = now
        next_state = {
            "last_sample_at": last_sample,
            "runtime": {name: {"state": row.get("state"), "health": row.get("health")} for name, row in snapshot.get("runtime", {}).items()},
            "coverage": {str(row.get("publication_id")): row.get("coverage_status") for row in snapshot.get("flyers", {}).get("publications", [])},
        }
        _write_json(STATE_PATH, next_state)
    except Exception:
        return


def history(*, hours: int = 168) -> list[dict[str, Any]]:
    cutoff = int(time.time()) - max(1, hours) * 3600
    return [row for row in _read_jsonl(HISTORY_PATH, limit=MAX_HISTORY) if int(row.get("at") or 0) >= cutoff]


def trends_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"samples": 0, "series": {}}
    keys = ("core_ms", "mobile_ms", "luna_cost_dkk", "luna_requests", "coverage_degraded", "quarantined", "retries")
    series = {key: [{"at": row.get("at"), "value": row.get(key)} for row in rows if row.get(key) is not None] for key in keys}
    return {"samples": len(rows), "from": rows[0].get("at"), "to": rows[-1].get("at"), "series": series}

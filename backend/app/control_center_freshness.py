from __future__ import annotations

import time
from typing import Any


def _age(timestamp: Any) -> int | None:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        return None
    return max(0, int(time.time()) - value) if value else None


def _row(name: str, timestamp: Any, health: str, detail: str) -> dict[str, Any]:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        value = 0
    return {
        "name": name,
        "at": value or None,
        "age_seconds": _age(value),
        "health": health,
        "detail": detail,
    }


def freshness_status(
    *,
    runtime: dict[str, dict[str, Any]],
    publications: list[dict[str, Any]],
    flyer_push_store: dict[str, Any],
    samsung: dict[str, Any],
    luna_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    now = int(time.time())

    push_runtime = runtime.get("flyer-push-worker", {}) if isinstance(runtime.get("flyer-push-worker"), dict) else {}
    push_payload = push_runtime.get("payload", {}) if isinstance(push_runtime.get("payload"), dict) else {}
    provider_at = int(push_payload.get("last_provider_check_at") or 0)
    provider_age = max(0, now - provider_at) if provider_at else None
    provider_health = "healthy" if provider_age is not None and provider_age <= 1800 else "attention" if provider_age is not None and provider_age <= 7200 else "stale"

    latest_publication = max((int(row.get("detected_at") or 0) for row in publications if isinstance(row, dict)), default=0)
    publication_age = max(0, now - latest_publication) if latest_publication else None
    # Flyers are normally weekly. A lack of a new generation for a few days is
    # not stale data; only a genuinely old catalogue set deserves attention.
    publication_health = "healthy" if publication_age is not None and publication_age <= 8 * 86400 else "attention" if publication_age is not None and publication_age <= 14 * 86400 else "stale"

    luna_runtime = runtime.get("luna-worker", {}) if isinstance(runtime.get("luna-worker"), dict) else {}
    luna_payload = luna_runtime.get("payload", {}) if isinstance(luna_runtime.get("payload"), dict) else {}
    coverage = luna_payload.get("coverage", {}) if isinstance(luna_payload.get("coverage"), dict) else {}
    pending = int(coverage.get("pending") or 0)
    latest_luna = max((int(row.get("at") or 0) for row in luna_events if isinstance(row, dict)), default=0)
    luna_age = max(0, now - latest_luna) if latest_luna else None
    if luna_runtime.get("health") == "error":
        luna_health, luna_detail = "error", "Workerfejl"
    elif pending == 0:
        luna_health, luna_detail = "healthy", "Idle er sundt: ingen obligatorisk coverage venter"
    elif luna_age is not None and luna_age <= 1800:
        luna_health, luna_detail = "healthy", f"{pending} pending og nylig aktivitet"
    else:
        luna_health, luna_detail = "attention", f"{pending} pending uden nylig Luna-aktivitet"

    apns_at = int(flyer_push_store.get("last_ready_delivery_at") or 0)
    # No push is expected unless a new quality-ready flyer exists. Worker health
    # is the operational signal; last delivery is informational freshness only.
    if push_runtime.get("health") == "error":
        apns_health, apns_detail = "error", "Flyer push worker har fejl"
    else:
        apns_health, apns_detail = "healthy", "Ingen levering forventes uden ny quality-ready avis"

    samsung_at = int(samsung.get("checked_at") or 0)
    samsung_age = max(0, now - samsung_at) if samsung_at else None
    if samsung.get("ok") and samsung_age is not None and samsung_age <= 900:
        samsung_health = "healthy"
    elif samsung_age is not None and samsung_age <= 1800:
        samsung_health = "attention"
    else:
        samsung_health = "stale"

    return [
        _row("Provider check", provider_at, provider_health, "Forventes mindst hvert 15. minut"),
        _row("Nyeste avisdata", latest_publication, publication_health, "Ugentlig cadence; op til 8 dage er normalt"),
        _row("Luna aktivitet", latest_luna, luna_health, luna_detail),
        _row("APNs levering", apns_at, apns_health, apns_detail),
        _row("Samsung validering", samsung_at, samsung_health, "Ekstern tokenvalidering caches og køres højst hvert 5. minut"),
    ]

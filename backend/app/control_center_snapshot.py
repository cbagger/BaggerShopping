from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from . import flyer_push, flyer_readiness, luna_cost_policy, luna_enrichment
from . import luna_member_coverage, luna_overlay, luna_resilient_strong_worker, luna_resilient_worker
from . import product_identity
from .control_center_catalog import IOS_RELEASE, catalog, dataflow
from .control_telemetry import all_heartbeats, read_heartbeat
from .households import LEGACY_HOUSEHOLD_ID, legacy_worker_context, load_store as load_households
from .mobile_offer_metadata import load_offer_metadata_store


PROBE_TIMEOUT_SECONDS = 3.0
RUNTIME_PROBE_TTL_SECONDS = 15.0
SAMSUNG_PROBE_TTL_SECONDS = 300.0

_runtime_probe_lock = asyncio.Lock()
_runtime_probe_cache: dict[str, dict[str, Any]] | None = None
_runtime_probe_at = 0.0
_samsung_probe_lock = asyncio.Lock()
_samsung_probe_cache: dict[str, Any] | None = None
_samsung_probe_at = 0.0


def _safe_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return value


def _file_info(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False, "updated_at": None, "size_bytes": 0}
    return {"exists": True, "updated_at": int(stat.st_mtime), "size_bytes": int(stat.st_size)}


def _age(timestamp: object) -> int | None:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        return None
    return max(0, int(time.time()) - value) if value else None


def _tone_for_status(status: str) -> str:
    key = status.casefold()
    if key in {"ok", "healthy", "running", "connected", "complete", "ready", "deployed", "available", "online"}:
        return "healthy"
    if key in {"error", "failed", "down", "unhealthy", "missing", "unavailable"}:
        return "error"
    if key in {"degraded", "stale", "refresh-needed", "interaction-required", "pending", "processing", "warning"}:
        return "attention"
    return "info"


def _probe_result(name: str, *, ok: bool, status_code: int | None, elapsed_ms: int | None, payload: Any = None, error: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "health": "healthy" if ok else "error",
        "state": "online" if ok else "unavailable",
        "status_code": status_code,
        "latency_ms": elapsed_ms,
        "payload": payload if isinstance(payload, dict) else {},
        "error": error,
        "checked_at": int(time.time()),
    }


async def _probe_json(name: str, url: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(url)
        elapsed = round((time.monotonic() - started) * 1000)
        payload: Any = {}
        if response.content and "application/json" in response.headers.get("content-type", ""):
            try:
                payload = response.json()
            except ValueError:
                payload = {}
        return _probe_result(
            name,
            ok=response.status_code == 200,
            status_code=response.status_code,
            elapsed_ms=elapsed,
            payload=payload,
            error=None if response.status_code == 200 else f"HTTP {response.status_code}",
        )
    except Exception as exc:
        return _probe_result(
            name,
            ok=False,
            status_code=None,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error=str(exc)[:300],
        )


async def runtime_probes(*, force: bool = False) -> dict[str, dict[str, Any]]:
    """Cheap internal process liveness; deliberately no Samsung/provider work."""
    global _runtime_probe_cache, _runtime_probe_at
    now = time.monotonic()
    if not force and _runtime_probe_cache is not None and now - _runtime_probe_at < RUNTIME_PROBE_TTL_SECONDS:
        return _runtime_probe_cache
    async with _runtime_probe_lock:
        now = time.monotonic()
        if not force and _runtime_probe_cache is not None and now - _runtime_probe_at < RUNTIME_PROBE_TTL_SECONDS:
            return _runtime_probe_cache
        core, mobile, login = await asyncio.gather(
            _probe_json("core-api", os.getenv("CONTROL_CENTER_CORE_LIVENESS", "http://bagger-shopping:8080/docs")),
            _probe_json("mobile-api", os.getenv("CONTROL_CENTER_MOBILE_LIVENESS", "http://mobile-api:8081/docs")),
            _probe_json("samsung-login-broker", os.getenv("CONTROL_CENTER_LOGIN_HEALTH", "http://samsung-login-broker:8090/health")),
        )
        _runtime_probe_cache = {row["name"]: row for row in (core, mobile, login)}
        _runtime_probe_at = time.monotonic()
        return _runtime_probe_cache


async def samsung_probe(*, force: bool = False) -> dict[str, Any]:
    """Validate Samsung through Core at most once every five minutes."""
    global _samsung_probe_cache, _samsung_probe_at
    now = time.monotonic()
    if not force and _samsung_probe_cache is not None and now - _samsung_probe_at < SAMSUNG_PROBE_TTL_SECONDS:
        return _samsung_probe_cache
    async with _samsung_probe_lock:
        now = time.monotonic()
        if not force and _samsung_probe_cache is not None and now - _samsung_probe_at < SAMSUNG_PROBE_TTL_SECONDS:
            return _samsung_probe_cache
        result = await _probe_json(
            "samsung-food",
            os.getenv("CONTROL_CENTER_SAMSUNG_HEALTH", "http://bagger-shopping:8080/api/health"),
        )
        _samsung_probe_cache = result
        _samsung_probe_at = time.monotonic()
        return result


def heartbeat_runtime(component: str) -> dict[str, Any]:
    heartbeat = read_heartbeat(component, stale_after=75)
    status = str(heartbeat.get("status") or "unknown")
    return {
        "name": component,
        "ok": status == "running",
        "health": _tone_for_status(status),
        "state": status,
        "status_code": None,
        "latency_ms": None,
        "payload": heartbeat.get("metrics") or {},
        "error": heartbeat.get("detail") if status in {"error", "stale", "degraded"} else None,
        "detail": heartbeat.get("detail"),
        "checked_at": heartbeat.get("updated_at"),
        "age_seconds": heartbeat.get("age_seconds"),
    }


def summarize_households() -> dict[str, Any]:
    store = load_households()
    households = store.get("households", {}) if isinstance(store.get("households"), dict) else {}
    summaries: list[dict[str, Any]] = []
    member_total = 0
    for household_id, value in households.items():
        if not isinstance(value, dict):
            continue
        members = [row for row in value.get("members", {}).values() if isinstance(row, dict)]
        owner = value.get("owner") if isinstance(value.get("owner"), dict) else None
        owner_is_separate = bool(household_id == LEGACY_HOUSEHOLD_ID and owner)
        member_count = len(members) + int(owner_is_separate and not any(row.get("role") == "owner" for row in members))
        member_total += member_count
        integrations = value.get("integrations", {}) if isinstance(value.get("integrations"), dict) else {}
        samsung = integrations.get("samsung_food", {}) if isinstance(integrations.get("samsung_food"), dict) else {}
        summaries.append({
            "name": str(value.get("name") or "Unavngiven familie"),
            "backend": str(value.get("list_backend") or "local"),
            "members": member_count,
            "local_items": len(value.get("items", [])) if isinstance(value.get("items"), list) else 0,
            "offer_metadata_records": len(value.get("offer_metadata", {})) if isinstance(value.get("offer_metadata"), dict) else 0,
            "product_preferences": len(value.get("product_preferences", {})) if isinstance(value.get("product_preferences"), dict) else 0,
            "samsung_status": str(samsung.get("status") or ("configured" if value.get("list_backend") == "samsung" else "not_connected")),
            "last_successful_sync": samsung.get("last_successful_sync"),
            "recovery_configured": bool(value.get("recovery_code_hash")),
        })
    return {
        "households": len(summaries),
        "members": member_total,
        "records": sorted(summaries, key=lambda row: row["name"].casefold()),
        "pending_invites": len(store.get("invites", {})) if isinstance(store.get("invites"), dict) else 0,
        "store": _file_info(Path(os.getenv("HOUSEHOLD_STORE_PATH", "/data/households.json"))),
    }


def summarize_offer_metadata() -> dict[str, Any]:
    try:
        legacy_worker_context()
        store = load_offer_metadata_store()
    except Exception:
        store = {}
    retailers: Counter[str] = Counter()
    pinned = 0
    with_offer_snapshot = 0
    for row in store.values():
        if not isinstance(row, dict):
            continue
        retailers[str(row.get("retailer") or "Ukendt")] += 1
        pinned += int(bool(row.get("pinned") or (row.get("offer_id") and row.get("publication_id"))))
        with_offer_snapshot += int(isinstance(row.get("offer_snapshot"), dict))
    return {
        "records": len(store),
        "pinned": pinned,
        "with_offer_snapshot": with_offer_snapshot,
        "retailers": dict(retailers.most_common()),
        "store": _file_info(Path(os.getenv("OFFER_METADATA_STORE_PATH", "/data/offer-metadata.json"))),
    }


def summarize_product_identity() -> dict[str, Any]:
    try:
        store = product_identity._read_store()
    except Exception:
        store = {}
    sections = {str(key): len(value) for key, value in store.items() if isinstance(value, (dict, list))}
    return {
        "sections": sections,
        "stored_rules": sum(sections.values()),
        "store": _file_info(product_identity.store_path()),
    }


def category_override_summary() -> dict[str, Any]:
    path = Path(os.getenv("CATEGORY_STORE_PATH", "/data/category-overrides.json"))
    payload = _safe_json(path, {})
    return {"records": len(payload) if isinstance(payload, dict) else 0, "store": _file_info(path)}


def luna_store_summary() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    store = luna_enrichment.load_store()
    events: list[dict[str, Any]] = []
    for row in store.get("events", [])[-80:]:
        if isinstance(row, dict):
            events.append({
                "at": int(row.get("at") or 0),
                "type": str(row.get("event") or "luna"),
                "status": str(row.get("status") or ""),
                "retailer": str(row.get("retailer") or ""),
                "detail": str(row.get("detail") or row.get("error") or "")[:300],
            })
    return store, events


def _publication_is_current(source: dict[str, Any]) -> bool:
    valid_until = str(source.get("valid_until") or "").strip()
    if not valid_until:
        return True
    try:
        return datetime.strptime(valid_until, "%d.%m.%Y").date() >= datetime.now().date()
    except ValueError:
        return True


def _exact_luna_stats(publication_id: str, fingerprint: str, *, luna_store: dict[str, Any], serving_rows: dict[str, Any]) -> dict[str, Any]:
    cache_row = serving_rows.get(publication_id)
    if not isinstance(cache_row, dict) or cache_row.get("fingerprint") != fingerprint or cache_row.get("verified") is not True:
        return {"available": False, "records": 0, "failed": 0, "member_prices": None}
    publication = luna_overlay._restore_publication(cache_row)
    if publication is None:
        return {"available": False, "records": 0, "failed": 0, "member_prices": None}
    records = luna_store.get("records", {}) if isinstance(luna_store.get("records"), dict) else {}
    stats = {"available": True, "records": 0, "failed": 0, "member_prices": 0}
    for offer in publication.structured_offers:
        row = records.get(luna_enrichment.offer_fingerprint(offer))
        if not isinstance(row, dict):
            continue
        stats["records"] += 1
        if row.get("status") == "failed":
            stats["failed"] += 1
        facts = row.get("facts")
        if row.get("status") == "completed" and isinstance(facts, dict) and isinstance(facts.get("member_price"), (int, float)) and not isinstance(facts.get("member_price"), bool):
            stats["member_prices"] += 1
    return stats


def current_publications(luna_store: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    readiness = flyer_readiness.load_store()
    coverage_store = luna_member_coverage._load()
    coverage_items = coverage_store.get("items", {}) if isinstance(coverage_store.get("items"), dict) else {}
    quarantine = luna_resilient_worker._load_quarantine()
    retry_state = luna_resilient_strong_worker._load_retry_state()
    serving_cache = luna_overlay._load_serving_cache()
    serving_rows = serving_cache.get("publications", {}) if isinstance(serving_cache.get("publications"), dict) else {}

    quarantine_by_publication: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in quarantine.values():
        if isinstance(row, dict):
            key = (str(row.get("publication_id") or ""), str(row.get("publication_fingerprint") or ""))
            quarantine_by_publication[key].append(row)
    retries_by_publication: Counter[tuple[str, str]] = Counter()
    for row in retry_state.values():
        if isinstance(row, dict):
            key = (str(row.get("publication_id") or ""), str(row.get("publication_fingerprint") or ""))
            retries_by_publication[key] += 1

    rows: list[dict[str, Any]] = []
    counts = {"pending": 0, "complete": 0, "degraded": 0, "not_tracked": 0}
    for publication_id, source in readiness.get("publications", {}).items():
        if not isinstance(source, dict) or not _publication_is_current(source):
            continue
        fingerprint = str(source.get("fingerprint") or "")
        key = luna_member_coverage.coverage_key(publication_id, fingerprint)
        cov = coverage_items.get(key) if fingerprint else None
        cov = cov if isinstance(cov, dict) else None
        cov_status = str(cov.get("status") or "not_tracked") if cov else "not_tracked"
        counts[cov_status if cov_status in counts else "not_tracked"] += 1
        total_pages = len(source.get("page_fingerprints", {})) if isinstance(source.get("page_fingerprints"), dict) else 0
        remaining = cov.get("pages_remaining") if cov else None
        if cov_status in {"complete", "degraded"}:
            done = total_pages
        elif isinstance(remaining, int) and total_pages:
            done = max(0, total_pages - remaining)
        else:
            done = 0
        progress = 100 if total_pages == 0 and cov_status in {"complete", "degraded"} else (round(done / total_pages * 100) if total_pages else 0)
        qrows = quarantine_by_publication.get((str(publication_id), fingerprint), [])
        qreasons = Counter(str(row.get("reason") or "unknown") for row in qrows)
        exact = _exact_luna_stats(str(publication_id), fingerprint, luna_store=luna_store, serving_rows=serving_rows)
        rows.append({
            "publication_id": str(publication_id),
            "retailer": str(source.get("retailer") or "Ukendt"),
            "title": str(source.get("title") or "Tilbudsavis"),
            "valid_from": source.get("valid_from"),
            "valid_until": source.get("valid_until"),
            "source_status": str(source.get("status") or "unknown"),
            "coverage_status": cov_status,
            "fingerprint": fingerprint,
            "pages_total": total_pages,
            "pages_done": done,
            "pages_remaining": remaining,
            "progress": progress,
            "pricing_remaining": cov.get("pricing_remaining") if cov else None,
            "member_fallback_remaining": cov.get("member_fallback_remaining") if cov else None,
            "hard_quarantined": int(cov.get("hard_quarantined") or 0) if cov else 0,
            "quarantine_count": len(qrows),
            "quarantine_reasons": dict(qreasons.most_common(8)),
            "retry_candidates": retries_by_publication.get((str(publication_id), fingerprint), 0),
            "member_prices_verified": exact["member_prices"],
            "luna_records": exact["records"],
            "luna_failed_records": exact["failed"],
            "luna_generation_stats_available": exact["available"],
            "detected_at": source.get("detected_at"),
            "ready_at": source.get("ready_at"),
            "coverage_updated_at": cov.get("updated_at") if cov else None,
            "last_error": source.get("last_error"),
        })
    order = {"pending": 0, "degraded": 1, "not_tracked": 2, "complete": 3}
    rows.sort(key=lambda row: (order.get(row["coverage_status"], 9), row["retailer"].casefold(), row["title"].casefold()))
    return rows, counts


def volume_status() -> dict[str, Any]:
    try:
        stats = os.statvfs("/data")
        total = stats.f_frsize * stats.f_blocks
        free = stats.f_frsize * stats.f_bavail
        used = max(0, total - free)
        return {"total_bytes": total, "used_bytes": used, "free_bytes": free, "used_percent": round(used / total * 100, 1) if total else 0}
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "free_bytes": None, "used_percent": None}


def release_info(control_center_version: str) -> dict[str, Any]:
    path = Path(os.getenv("KURV_DEPLOYED_COMMIT_PATH", "/data/deployed-commit.txt"))
    try:
        commit = path.read_text("utf-8").strip()
    except OSError:
        commit = os.getenv("KURV_DEPLOYED_COMMIT", "").strip()
    return {"commit": commit or "unknown", "control_center": control_center_version, "ios": dict(IOS_RELEASE), "deployment_marker": _file_info(path)}


def derive_component_states(runtime: dict[str, dict[str, Any]], *, samsung: dict[str, Any], luna: dict[str, Any], flyer_push_store: dict[str, Any], household_summary: dict[str, Any], current_coverage: dict[str, int]) -> list[dict[str, Any]]:
    components = catalog()
    states: dict[str, dict[str, Any]] = {}
    for component_id in ("core-api", "mobile-api", "samsung-login-broker"):
        row = runtime.get(component_id, {})
        latency = row.get("latency_ms")
        states[component_id] = {"health": row.get("health", "error"), "state": row.get("state", "unknown"), "detail": row.get("error") or (f"{latency} ms" if latency is not None else None)}
    for component_id in ("luna-worker", "flyer-push-worker", "shopping-cleanup-worker"):
        row = runtime.get(component_id, {})
        states[component_id] = {"health": row.get("health", "attention"), "state": row.get("state", "unknown"), "detail": row.get("detail") or row.get("error")}
    states["control-center"] = {"health": "healthy", "state": "online", "detail": "Local-only · read-only"}

    samsung_payload = samsung.get("payload", {}) if isinstance(samsung.get("payload"), dict) else {}
    samsung_auth = str(samsung_payload.get("samsung_auth") or "unknown")
    if samsung.get("ok") and samsung_auth == "ok":
        samsung_health, samsung_state = "healthy", "connected"
    elif samsung.get("ok"):
        samsung_health, samsung_state = "attention", samsung_auth
    else:
        samsung_health, samsung_state = "attention", "validation-unavailable"
    states["samsung-food"] = {"health": samsung_health, "state": samsung_state, "detail": "Verificeres højst hvert 5. minut"}
    states["samsung-auth"] = {"health": samsung_health, "state": samsung_state, "detail": "Samsung token state"}
    states["grpc-web"] = {"health": samsung_health, "state": "available" if samsung_health == "healthy" else "attention", "detail": "Samsung transport"}

    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    budget, spent, remaining = float(usage.get("budget_dkk") or 0), float(usage.get("estimated_cost_dkk") or 0), float(usage.get("remaining_dkk") or 0)
    if not luna.get("enabled"):
        openai_health, openai_state = "attention", "disabled"
    elif not luna.get("api_key_configured"):
        openai_health, openai_state = "error", "missing-api-key"
    elif remaining <= 0 and budget > 0:
        openai_health, openai_state = "attention", "budget-exhausted"
    else:
        openai_health, openai_state = "healthy", "available"
    states["openai-luna"] = {"health": openai_health, "state": openai_state, "detail": f"{spent:.2f} / {budget:.0f} kr." if budget else f"{spent:.2f} kr."}

    push_hb = runtime.get("flyer-push-worker", {})
    provider_age = _age(push_hb.get("payload", {}).get("last_provider_check_at"))
    provider_health = "healthy" if provider_age is not None and provider_age < 7200 else "attention"
    states["provider-tjek"] = {"health": provider_health, "state": "active" if provider_health == "healthy" else "stale", "detail": f"Seneste provider-check {provider_age}s siden" if provider_age is not None else "Afventer første provider-check"}
    enabled_devices = sum(1 for row in flyer_push_store.get("devices", {}).values() if isinstance(row, dict) and row.get("enabled"))
    apns_health = runtime.get("flyer-push-worker", {}).get("health", "attention")
    states["apple-apns"] = {"health": apns_health, "state": "configured" if enabled_devices else "no-enabled-devices", "detail": f"{enabled_devices} aktive push-enheder"}

    inherit = {
        "household-engine": "mobile-api", "shopping-sync": "core-api", "offer-metadata": "mobile-api", "mobile-offers": "mobile-api",
        "flyer-adapters": "mobile-api", "flyer-readiness": "luna-worker", "flyer-serving": "mobile-api", "flyer-intelligence": "mobile-api",
        "member-pricing": "mobile-api", "luna-semantic-audit": "luna-worker", "luna-semantic-guards": "luna-worker", "luna-pricing-reader": "mobile-api",
        "member-coverage": "luna-worker", "luna-cost-policy": "luna-worker", "product-identity": "mobile-api", "variant-engine": "mobile-api",
    }
    for component_id, parent in inherit.items():
        parent_state = states.get(parent, {"health": "info", "state": "available"})
        states[component_id] = {"health": parent_state.get("health", "info"), "state": "available" if parent_state.get("health") == "healthy" else parent_state.get("state", "unknown"), "detail": f"Hosted by {parent}"}
    if current_coverage.get("degraded", 0) > 0:
        states["member-coverage"] = {"health": "attention", "state": "degraded-present", "detail": f"{current_coverage.get('degraded', 0)} aktuelle avis-generationer degraded"}
    if household_summary.get("households", 0) <= 0:
        states["household-engine"] = {"health": "attention", "state": "no-households", "detail": "Ingen familier registreret"}
    for component in components:
        if component.get("runtime") == "ios":
            states[component["id"]] = {"health": "info", "state": "deployed", "detail": f"iOS {IOS_RELEASE['version']} · build {IOS_RELEASE['build']} · runtime på telefonen"}
    return [{**component, **states.get(component["id"], {"health": "info", "state": "available", "detail": None})} for component in components]


def alerts(runtime: dict[str, dict[str, Any]], components: list[dict[str, Any]], publications: list[dict[str, Any]], luna: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in runtime.values():
        if row.get("health") == "error":
            result.append({"severity": "critical", "title": f"{row.get('name')} er utilgængelig", "detail": row.get("error")})
        elif row.get("health") == "attention":
            result.append({"severity": "warning", "title": f"{row.get('name')} kræver opmærksomhed", "detail": row.get("detail") or row.get("error")})
    degraded = [row for row in publications if row.get("coverage_status") == "degraded"]
    if degraded:
        result.append({"severity": "warning", "title": f"{len(degraded)} aktuelle avis-generationer er degraded", "detail": "Åbn Luna & aviser for konkrete quarantine-årsager."})
    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    budget, spent = float(usage.get("budget_dkk") or 0), float(usage.get("estimated_cost_dkk") or 0)
    if budget and spent / budget >= 0.8:
        result.append({"severity": "warning", "title": "Luna-budget nærmer sig loftet", "detail": f"{spent:.2f} af {budget:.2f} kr. brugt denne måned."})
    for component in components:
        if component.get("health") == "error" and not any(component.get("name") in str(row.get("title")) for row in result):
            result.append({"severity": "critical", "title": f"{component.get('name')} har fejlstatus", "detail": component.get("detail")})
    return result[:20]


def timeline(luna_events: list[dict[str, Any]], publications: list[dict[str, Any]], runtime: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    events = list(luna_events)
    for row in publications:
        if row.get("coverage_status") in {"complete", "degraded"} and row.get("coverage_updated_at"):
            events.append({"at": int(row["coverage_updated_at"]), "type": "coverage", "status": row["coverage_status"], "retailer": row["retailer"], "detail": row["title"]})
    for name, row in runtime.items():
        if row.get("checked_at"):
            events.append({"at": int(row["checked_at"]), "type": "runtime", "status": row.get("state"), "retailer": "", "detail": name})
    events = [row for row in events if int(row.get("at") or 0) > 0]
    events.sort(key=lambda row: int(row.get("at") or 0), reverse=True)
    return events[:80]


async def build_snapshot(*, control_center_version: str) -> dict[str, Any]:
    probes, samsung = await asyncio.gather(runtime_probes(), samsung_probe())
    runtime = {
        **probes,
        "luna-worker": heartbeat_runtime("luna-worker"),
        "flyer-push-worker": heartbeat_runtime("flyer-push-worker"),
        "shopping-cleanup-worker": heartbeat_runtime("shopping-cleanup-worker"),
    }
    luna = luna_enrichment.status_payload()
    luna_store, luna_events = luna_store_summary()
    coverage = luna_member_coverage.status_payload()
    readiness = flyer_readiness.status_payload()
    publications, current_coverage = current_publications(luna_store)
    households = summarize_households()
    offer_metadata = summarize_offer_metadata()
    identity = summarize_product_identity()
    category_overrides = category_override_summary()
    flyer_push_store = flyer_push._load()
    retry_state = luna_resilient_strong_worker._load_retry_state()
    quarantine = luna_resilient_worker._load_quarantine()
    cost_policy = luna_cost_policy.status_payload()
    components = derive_component_states(runtime, samsung=samsung, luna=luna, flyer_push_store=flyer_push_store, household_summary=households, current_coverage=current_coverage)
    active_alerts = alerts(runtime, components, publications, luna)
    activity = timeline(luna_events, publications, runtime)
    health_counts = Counter(component.get("health", "info") for component in components)
    runtime_errors = sum(1 for row in runtime.values() if row.get("health") == "error")
    overall = "critical" if runtime_errors else "attention" if active_alerts else "healthy"
    return {
        "generated_at": int(time.time()),
        "generated_iso": datetime.now().astimezone().isoformat(),
        "overall": {"status": overall, "components": len(components), "health_counts": dict(health_counts), "alerts": len(active_alerts)},
        "release": release_info(control_center_version),
        "runtime": runtime,
        "components": components,
        "dataflow": dataflow(),
        "alerts": active_alerts,
        "integrations": {"samsung": samsung},
        "luna": {**luna, "coverage": coverage, "current_coverage": current_coverage, "quarantined": len(quarantine), "retry_candidates": len(retry_state), "cost_policy": cost_policy, "store": _file_info(luna_enrichment.STORE_PATH), "events_stored": len(luna_store.get("events", [])) if isinstance(luna_store.get("events"), list) else 0},
        "flyers": {
            "readiness": readiness,
            "current_coverage": current_coverage,
            "history_coverage": coverage.get("counts", {}),
            "publications": publications,
            "push": {
                "initialized": bool(flyer_push_store.get("initialized")),
                "enabled_devices": sum(1 for row in flyer_push_store.get("devices", {}).values() if isinstance(row, dict) and row.get("enabled")),
                "registered_devices": len(flyer_push_store.get("devices", {})) if isinstance(flyer_push_store.get("devices"), dict) else 0,
                "last_check_at": flyer_push_store.get("last_check_at"),
                "last_ready_delivery_at": flyer_push_store.get("last_ready_delivery_at"),
                "seen_publications": len(flyer_push_store.get("seen_publications", [])),
            },
        },
        "data": {"households": households, "offer_metadata": offer_metadata, "product_identity": identity, "category_overrides": category_overrides, "volume": volume_status()},
        "telemetry": {"heartbeats": all_heartbeats(), "timeline": activity},
    }

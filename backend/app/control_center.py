from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import flyer_push
from . import flyer_readiness
from . import luna_cost_policy
from . import luna_enrichment
from . import luna_member_coverage
from . import luna_resilient_strong_worker
from . import luna_resilient_worker
from . import product_identity
from .control_center_catalog import IOS_RELEASE, catalog, dataflow
from .control_telemetry import all_heartbeats, read_heartbeat
from .households import LEGACY_HOUSEHOLD_ID, legacy_worker_context, load_store as load_households
from .mobile_offer_metadata import load_offer_metadata_store


APP_VERSION = "1.0.0"
STATIC_DIR = Path(__file__).with_name("control_center_static")
SNAPSHOT_TTL_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 3.0

app = FastAPI(
    title="Kurv Control Center",
    version=APP_VERSION,
    description="Local-only, read-only observability for the complete Kurv system.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

_snapshot_lock = asyncio.Lock()
_snapshot_cache: dict[str, Any] | None = None
_snapshot_cached_at = 0.0


def _client_is_local(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.split("%", 1)[0].strip().casefold()
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Control Center is intentionally a LAN product, not an internet admin UI.
    # The compose file exposes only a raw QNAP port and this second boundary
    # rejects non-private source addresses even if somebody later forwards it.
    if not _client_is_local(request.client.host if request.client else None):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "detail": "Kurv Control Center er kun tilgængeligt lokalt."},
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "kurv-control-center", "version": APP_VERSION, "local_only": True}


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
    if key in {"ok", "healthy", "running", "connected", "complete", "ready", "deployed", "available"}:
        return "healthy"
    if key in {"error", "failed", "down", "unhealthy", "missing"}:
        return "error"
    if key in {"degraded", "stale", "refresh-needed", "interaction-required", "pending", "processing", "warning"}:
        return "attention"
    return "info"


def _probe_result(name: str, *, ok: bool, status_code: int | None, elapsed_ms: int | None, payload: Any = None, error: str | None = None) -> dict[str, Any]:
    state = "healthy" if ok else "error"
    return {
        "name": name,
        "ok": ok,
        "health": state,
        "state": "online" if ok else "unavailable",
        "status_code": status_code,
        "latency_ms": elapsed_ms,
        "payload": payload if isinstance(payload, dict) else {},
        "error": error,
        "checked_at": int(time.time()),
    }


async def _probe_json(
    name: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
        elapsed = round((time.monotonic() - started) * 1000)
        payload: Any = {}
        if response.content:
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


async def _runtime_probes() -> dict[str, dict[str, Any]]:
    mobile_token = os.getenv("MOBILE_API_TOKEN", "").strip()
    mobile_headers = {"Authorization": f"Bearer {mobile_token}"} if mobile_token else None
    core, mobile, login = await asyncio.gather(
        _probe_json("core-api", os.getenv("CONTROL_CENTER_CORE_HEALTH", "http://bagger-shopping:8080/api/health")),
        _probe_json(
            "mobile-api",
            os.getenv("CONTROL_CENTER_MOBILE_HEALTH", "http://mobile-api:8081/api/mobile/v1/offers/health"),
            headers=mobile_headers,
        ),
        _probe_json(
            "samsung-login-broker",
            os.getenv("CONTROL_CENTER_LOGIN_HEALTH", "http://samsung-login-broker:8090/health"),
        ),
    )
    return {row["name"]: row for row in (core, mobile, login)}


def _heartbeat_runtime(component: str) -> dict[str, Any]:
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


def _summarize_households() -> dict[str, Any]:
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
        samsung = value.get("integrations", {}).get("samsung_food", {}) if isinstance(value.get("integrations"), dict) else {}
        summaries.append({
            "name": str(value.get("name") or "Unavngiven familie"),
            "backend": str(value.get("list_backend") or "local"),
            "members": member_count,
            "local_items": len(value.get("items", [])) if isinstance(value.get("items"), list) else 0,
            "offer_metadata_records": len(value.get("offer_metadata", {})) if isinstance(value.get("offer_metadata"), dict) else 0,
            "product_preferences": len(value.get("product_preferences", {})) if isinstance(value.get("product_preferences"), dict) else 0,
            "samsung_status": str(samsung.get("status") or ("connected" if value.get("list_backend") == "samsung" else "not_connected")),
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


def _summarize_offer_metadata() -> dict[str, Any]:
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
        retailer = str(row.get("retailer") or "Ukendt")
        retailers[retailer] += 1
        pinned += int(bool(row.get("pinned") or (row.get("offer_id") and row.get("publication_id"))))
        with_offer_snapshot += int(isinstance(row.get("offer_snapshot"), dict))
    return {
        "records": len(store),
        "pinned": pinned,
        "with_offer_snapshot": with_offer_snapshot,
        "retailers": dict(retailers.most_common()),
        "store": _file_info(Path(os.getenv("OFFER_METADATA_STORE_PATH", "/data/offer-metadata.json"))),
    }


def _summarize_product_identity() -> dict[str, Any]:
    try:
        store = product_identity._read_store()
    except Exception:
        store = {}
    sections: dict[str, int] = {}
    for key, value in store.items():
        if isinstance(value, (dict, list)):
            sections[str(key)] = len(value)
    return {
        "sections": sections,
        "stored_rules": sum(sections.values()),
        "store": _file_info(product_identity.store_path()),
    }


def _category_override_summary() -> dict[str, Any]:
    path = Path(os.getenv("CATEGORY_STORE_PATH", "/data/category-overrides.json"))
    payload = _safe_json(path, {})
    return {
        "records": len(payload) if isinstance(payload, dict) else 0,
        "store": _file_info(path),
    }


def _luna_record_stats() -> tuple[dict[str, Any], dict[str, dict[str, int]], list[dict[str, Any]]]:
    store = luna_enrichment.load_store()
    by_publication: dict[str, dict[str, int]] = defaultdict(lambda: {
        "records": 0,
        "completed": 0,
        "failed": 0,
        "no_change": 0,
        "member_prices": 0,
    })
    for row in store.get("records", {}).values():
        if not isinstance(row, dict):
            continue
        publication_id = str(row.get("publication_id") or "")
        if publication_id:
            stats = by_publication[publication_id]
            stats["records"] += 1
            status = str(row.get("status") or "")
            if status == "completed":
                stats["completed"] += 1
            elif status == "failed":
                stats["failed"] += 1
            elif status == "no-change":
                stats["no_change"] += 1
            facts = row.get("facts")
            if isinstance(facts, dict) and isinstance(facts.get("member_price"), (int, float)) and not isinstance(facts.get("member_price"), bool):
                stats["member_prices"] += 1

    events: list[dict[str, Any]] = []
    for row in store.get("events", [])[-80:]:
        if not isinstance(row, dict):
            continue
        events.append({
            "at": int(row.get("at") or 0),
            "type": str(row.get("event") or "luna"),
            "status": str(row.get("status") or ""),
            "retailer": str(row.get("retailer") or ""),
            "detail": str(row.get("detail") or row.get("error") or "")[:300],
        })
    return store, dict(by_publication), events


def _current_publications(
    luna_by_publication: dict[str, dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    readiness = flyer_readiness.load_store()
    coverage_store = luna_member_coverage._load()
    coverage_items = coverage_store.get("items", {}) if isinstance(coverage_store.get("items"), dict) else {}
    quarantine = luna_resilient_worker._load_quarantine()
    retry_state = luna_resilient_strong_worker._load_retry_state()

    quarantine_by_publication: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in quarantine.values():
        if not isinstance(row, dict):
            continue
        key = (str(row.get("publication_id") or ""), str(row.get("publication_fingerprint") or ""))
        quarantine_by_publication[key].append(row)

    retries_by_publication: Counter[tuple[str, str]] = Counter()
    for row in retry_state.values():
        if not isinstance(row, dict):
            continue
        key = (str(row.get("publication_id") or ""), str(row.get("publication_fingerprint") or ""))
        retries_by_publication[key] += 1

    rows: list[dict[str, Any]] = []
    current_counts = {"pending": 0, "complete": 0, "degraded": 0, "not_tracked": 0}
    for publication_id, source in readiness.get("publications", {}).items():
        if not isinstance(source, dict):
            continue
        fingerprint = str(source.get("fingerprint") or "")
        coverage_key = luna_member_coverage.coverage_key(publication_id, fingerprint)
        coverage = coverage_items.get(coverage_key) if fingerprint else None
        coverage = coverage if isinstance(coverage, dict) else None
        coverage_status = str(coverage.get("status") or "not_tracked") if coverage else "not_tracked"
        if coverage_status not in current_counts:
            current_counts["not_tracked"] += 1
        else:
            current_counts[coverage_status] += 1

        total_pages = len(source.get("page_fingerprints", {})) if isinstance(source.get("page_fingerprints"), dict) else 0
        remaining = coverage.get("pages_remaining") if coverage else None
        if coverage_status in {"complete", "degraded"}:
            pages_done = total_pages
        elif isinstance(remaining, int) and total_pages:
            pages_done = max(0, total_pages - remaining)
        else:
            pages_done = 0
        progress = 100 if total_pages == 0 and coverage_status in {"complete", "degraded"} else (
            round(pages_done / total_pages * 100) if total_pages else 0
        )

        qrows = quarantine_by_publication.get((str(publication_id), fingerprint), [])
        qreasons = Counter(str(row.get("reason") or "unknown") for row in qrows)
        luna_stats = luna_by_publication.get(str(publication_id), {})
        rows.append({
            "publication_id": str(publication_id),
            "retailer": str(source.get("retailer") or "Ukendt"),
            "title": str(source.get("title") or "Tilbudsavis"),
            "valid_from": source.get("valid_from"),
            "valid_until": source.get("valid_until"),
            "source_status": str(source.get("status") or "unknown"),
            "coverage_status": coverage_status,
            "fingerprint": fingerprint,
            "pages_total": total_pages,
            "pages_done": pages_done,
            "pages_remaining": remaining,
            "progress": progress,
            "pricing_remaining": coverage.get("pricing_remaining") if coverage else None,
            "member_fallback_remaining": coverage.get("member_fallback_remaining") if coverage else None,
            "hard_quarantined": int(coverage.get("hard_quarantined") or 0) if coverage else 0,
            "quarantine_count": len(qrows),
            "quarantine_reasons": dict(qreasons.most_common(8)),
            "retry_candidates": retries_by_publication.get((str(publication_id), fingerprint), 0),
            "member_prices_verified": int(luna_stats.get("member_prices") or 0),
            "luna_records": int(luna_stats.get("records") or 0),
            "luna_failed_records": int(luna_stats.get("failed") or 0),
            "detected_at": source.get("detected_at"),
            "ready_at": source.get("ready_at"),
            "coverage_updated_at": coverage.get("updated_at") if coverage else None,
            "last_error": source.get("last_error"),
        })

    order = {"pending": 0, "degraded": 1, "not_tracked": 2, "complete": 3}
    rows.sort(key=lambda row: (
        order.get(row["coverage_status"], 9),
        row["retailer"].casefold(),
        row["title"].casefold(),
    ))
    return rows, current_counts


def _volume_status() -> dict[str, Any]:
    try:
        stats = os.statvfs("/data")
        total = stats.f_frsize * stats.f_blocks
        free = stats.f_frsize * stats.f_bavail
        used = max(0, total - free)
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": round((used / total * 100), 1) if total else 0,
        }
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "free_bytes": None, "used_percent": None}


def _release_info() -> dict[str, Any]:
    deployed_path = Path(os.getenv("KURV_DEPLOYED_COMMIT_PATH", "/data/deployed-commit.txt"))
    try:
        commit = deployed_path.read_text("utf-8").strip()
    except OSError:
        commit = os.getenv("KURV_DEPLOYED_COMMIT", "").strip()
    return {
        "commit": commit or "unknown",
        "control_center": APP_VERSION,
        "ios": dict(IOS_RELEASE),
        "deployment_marker": _file_info(deployed_path),
    }


def _derive_component_states(
    runtime: dict[str, dict[str, Any]],
    *,
    luna: dict[str, Any],
    flyer_push_store: dict[str, Any],
    household_summary: dict[str, Any],
    current_coverage: dict[str, int],
) -> list[dict[str, Any]]:
    components = catalog()
    state_by_id: dict[str, dict[str, Any]] = {}

    for component_id in ("core-api", "mobile-api", "samsung-login-broker"):
        row = runtime.get(component_id, {})
        state_by_id[component_id] = {
            "health": row.get("health", "error"),
            "state": row.get("state", "unknown"),
            "detail": row.get("error") or f"{row.get('latency_ms')} ms" if row.get("latency_ms") is not None else row.get("error"),
        }

    for component_id in ("luna-worker", "flyer-push-worker", "shopping-cleanup-worker"):
        row = runtime.get(component_id, {})
        state_by_id[component_id] = {
            "health": row.get("health", "attention"),
            "state": row.get("state", "unknown"),
            "detail": row.get("detail") or row.get("error"),
        }

    state_by_id["control-center"] = {"health": "healthy", "state": "online", "detail": "Local-only · read-only"}

    core_payload = runtime.get("core-api", {}).get("payload", {})
    samsung_auth = str(core_payload.get("samsung_auth") or "unknown")
    samsung_health = "healthy" if samsung_auth == "ok" else "attention"
    state_by_id["samsung-food"] = {"health": samsung_health, "state": samsung_auth, "detail": "Family Hub shopping list"}
    state_by_id["samsung-auth"] = {"health": samsung_health, "state": samsung_auth, "detail": "Token validation via Core API"}
    state_by_id["grpc-web"] = {"health": samsung_health, "state": "available" if samsung_auth == "ok" else "attention", "detail": "Samsung transport"}

    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    budget = float(usage.get("budget_dkk") or 0)
    spent = float(usage.get("estimated_cost_dkk") or 0)
    remaining = float(usage.get("remaining_dkk") or 0)
    if not luna.get("enabled"):
        openai_health, openai_state = "attention", "disabled"
    elif not luna.get("api_key_configured"):
        openai_health, openai_state = "error", "missing-api-key"
    elif remaining <= 0 and budget > 0:
        openai_health, openai_state = "attention", "budget-exhausted"
    else:
        openai_health, openai_state = "healthy", "available"
    state_by_id["openai-luna"] = {
        "health": openai_health,
        "state": openai_state,
        "detail": f"{spent:.2f} / {budget:.0f} kr." if budget else f"{spent:.2f} kr.",
    }

    push_hb = runtime.get("flyer-push-worker", {})
    last_provider_check = push_hb.get("payload", {}).get("last_provider_check_at")
    provider_age = _age(last_provider_check)
    provider_health = "healthy" if provider_age is not None and provider_age < 7200 else "attention"
    state_by_id["provider-tjek"] = {
        "health": provider_health,
        "state": "active" if provider_health == "healthy" else "stale",
        "detail": f"Seneste provider-check {provider_age}s siden" if provider_age is not None else "Afventer første provider-check",
    }

    enabled_devices = sum(
        1 for row in flyer_push_store.get("devices", {}).values()
        if isinstance(row, dict) and row.get("enabled")
    )
    apns_health = runtime.get("flyer-push-worker", {}).get("health", "attention")
    state_by_id["apple-apns"] = {
        "health": apns_health,
        "state": "configured" if enabled_devices else "no-enabled-devices",
        "detail": f"{enabled_devices} aktive push-enheder",
    }

    inherit = {
        "household-engine": "mobile-api",
        "shopping-sync": "core-api",
        "offer-metadata": "mobile-api",
        "mobile-offers": "mobile-api",
        "flyer-adapters": "mobile-api",
        "flyer-readiness": "luna-worker",
        "flyer-serving": "mobile-api",
        "flyer-intelligence": "mobile-api",
        "member-pricing": "mobile-api",
        "luna-semantic-audit": "luna-worker",
        "luna-semantic-guards": "luna-worker",
        "luna-pricing-reader": "mobile-api",
        "member-coverage": "luna-worker",
        "luna-cost-policy": "luna-worker",
        "product-identity": "mobile-api",
        "variant-engine": "mobile-api",
    }
    for component_id, parent in inherit.items():
        parent_state = state_by_id.get(parent, {"health": "info", "state": "available"})
        state_by_id[component_id] = {
            "health": parent_state.get("health", "info"),
            "state": "active" if parent_state.get("health") == "healthy" else parent_state.get("state", "unknown"),
            "detail": None,
        }

    if current_coverage.get("degraded", 0) > 0:
        state_by_id["member-coverage"] = {
            "health": "attention",
            "state": "degraded-present",
            "detail": f"{current_coverage.get('degraded', 0)} aktuelle avis-generationer degraded",
        }

    if household_summary.get("households", 0) <= 0:
        state_by_id["household-engine"] = {"health": "attention", "state": "no-households", "detail": "Ingen familier registreret"}

    for component in components:
        if component.get("runtime") == "ios":
            state_by_id[component["id"]] = {
                "health": "info",
                "state": "deployed",
                "detail": f"iOS {IOS_RELEASE['version']} · build {IOS_RELEASE['build']} · runtime på telefonen",
            }

    result: list[dict[str, Any]] = []
    for component in components:
        state = state_by_id.get(component["id"], {"health": "info", "state": "available", "detail": None})
        result.append({**component, **state})
    return result


def _alerts(
    runtime: dict[str, dict[str, Any]],
    components: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    luna: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for row in runtime.values():
        if row.get("health") == "error":
            alerts.append({"severity": "critical", "title": f"{row.get('name')} er utilgængelig", "detail": row.get("error")})
        elif row.get("health") == "attention":
            alerts.append({"severity": "warning", "title": f"{row.get('name')} kræver opmærksomhed", "detail": row.get("detail") or row.get("error")})

    degraded = [row for row in publications if row.get("coverage_status") == "degraded"]
    if degraded:
        alerts.append({
            "severity": "warning",
            "title": f"{len(degraded)} aktuelle avis-generationer er degraded",
            "detail": "Åbn Luna & aviser for konkrete quarantine-årsager.",
        })

    usage = luna.get("usage", {}) if isinstance(luna.get("usage"), dict) else {}
    budget = float(usage.get("budget_dkk") or 0)
    spent = float(usage.get("estimated_cost_dkk") or 0)
    if budget and spent / budget >= 0.8:
        alerts.append({
            "severity": "warning",
            "title": "Luna-budget nærmer sig loftet",
            "detail": f"{spent:.2f} af {budget:.2f} kr. brugt denne måned.",
        })

    for component in components:
        if component.get("health") == "error" and not any(component.get("name") in str(a.get("title")) for a in alerts):
            alerts.append({"severity": "critical", "title": f"{component.get('name')} har fejlstatus", "detail": component.get("detail")})
    return alerts[:20]


def _timeline(
    luna_events: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    runtime: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    events = list(luna_events)
    for row in publications:
        if row.get("coverage_status") in {"complete", "degraded"} and row.get("coverage_updated_at"):
            events.append({
                "at": int(row["coverage_updated_at"]),
                "type": "coverage",
                "status": row["coverage_status"],
                "retailer": row["retailer"],
                "detail": row["title"],
            })
    for name, row in runtime.items():
        if row.get("checked_at"):
            events.append({
                "at": int(row["checked_at"]),
                "type": "runtime",
                "status": row.get("state"),
                "retailer": "",
                "detail": name,
            })
    events = [row for row in events if int(row.get("at") or 0) > 0]
    events.sort(key=lambda row: int(row.get("at") or 0), reverse=True)
    return events[:80]


async def build_snapshot() -> dict[str, Any]:
    probes = await _runtime_probes()
    runtime = {
        **probes,
        "luna-worker": _heartbeat_runtime("luna-worker"),
        "flyer-push-worker": _heartbeat_runtime("flyer-push-worker"),
        "shopping-cleanup-worker": _heartbeat_runtime("shopping-cleanup-worker"),
    }

    luna = luna_enrichment.status_payload()
    luna_store, luna_by_publication, luna_events = _luna_record_stats()
    coverage = luna_member_coverage.status_payload()
    readiness = flyer_readiness.status_payload()
    publications, current_coverage = _current_publications(luna_by_publication)
    households = _summarize_households()
    offer_metadata = _summarize_offer_metadata()
    identity = _summarize_product_identity()
    category_overrides = _category_override_summary()
    flyer_push_store = flyer_push._load()
    retry_state = luna_resilient_strong_worker._load_retry_state()
    quarantine = luna_resilient_worker._load_quarantine()
    cost_policy = luna_cost_policy.status_payload()

    components = _derive_component_states(
        runtime,
        luna=luna,
        flyer_push_store=flyer_push_store,
        household_summary=households,
        current_coverage=current_coverage,
    )
    alerts = _alerts(runtime, components, publications, luna)
    timeline = _timeline(luna_events, publications, runtime)

    health_counts = Counter(component.get("health", "info") for component in components)
    runtime_errors = sum(1 for row in runtime.values() if row.get("health") == "error")
    overall = "critical" if runtime_errors else "attention" if alerts else "healthy"

    return {
        "generated_at": int(time.time()),
        "generated_iso": datetime.now().astimezone().isoformat(),
        "overall": {
            "status": overall,
            "components": len(components),
            "health_counts": dict(health_counts),
            "alerts": len(alerts),
        },
        "release": _release_info(),
        "runtime": runtime,
        "components": components,
        "dataflow": dataflow(),
        "alerts": alerts,
        "luna": {
            **luna,
            "coverage": coverage,
            "current_coverage": current_coverage,
            "quarantined": len(quarantine),
            "retry_candidates": len(retry_state),
            "cost_policy": cost_policy,
            "store": _file_info(luna_enrichment.STORE_PATH),
            "events_stored": len(luna_store.get("events", [])) if isinstance(luna_store.get("events"), list) else 0,
        },
        "flyers": {
            "readiness": readiness,
            "current_coverage": current_coverage,
            "history_coverage": coverage.get("counts", {}),
            "publications": publications,
            "push": {
                "initialized": bool(flyer_push_store.get("initialized")),
                "enabled_devices": sum(
                    1 for row in flyer_push_store.get("devices", {}).values()
                    if isinstance(row, dict) and row.get("enabled")
                ),
                "registered_devices": len(flyer_push_store.get("devices", {})) if isinstance(flyer_push_store.get("devices"), dict) else 0,
                "last_check_at": flyer_push_store.get("last_check_at"),
                "last_ready_delivery_at": flyer_push_store.get("last_ready_delivery_at"),
                "seen_publications": len(flyer_push_store.get("seen_publications", [])),
            },
        },
        "data": {
            "households": households,
            "offer_metadata": offer_metadata,
            "product_identity": identity,
            "category_overrides": category_overrides,
            "volume": _volume_status(),
        },
        "telemetry": {
            "heartbeats": all_heartbeats(),
            "timeline": timeline,
        },
    }


async def snapshot(*, force: bool = False) -> dict[str, Any]:
    global _snapshot_cache, _snapshot_cached_at
    now = time.monotonic()
    if not force and _snapshot_cache is not None and now - _snapshot_cached_at < SNAPSHOT_TTL_SECONDS:
        return _snapshot_cache
    async with _snapshot_lock:
        now = time.monotonic()
        if not force and _snapshot_cache is not None and now - _snapshot_cached_at < SNAPSHOT_TTL_SECONDS:
            return _snapshot_cache
        value = await build_snapshot()
        _snapshot_cache = value
        _snapshot_cached_at = time.monotonic()
        return value


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return await snapshot()


@app.get("/api/architecture")
async def api_architecture() -> dict[str, Any]:
    return {"components": catalog(), "dataflow": dataflow(), "ios": dict(IOS_RELEASE)}


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    async def stream():
        last_signature = None
        while True:
            if await request.is_disconnected():
                break
            payload = await snapshot()
            signature = json.dumps(
                {
                    "generated_at": payload.get("generated_at"),
                    "overall": payload.get("overall"),
                    "coverage": payload.get("luna", {}).get("current_coverage"),
                    "usage": payload.get("luna", {}).get("usage"),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature != last_signature:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                yield f"event: snapshot\ndata: {data}\n\n"
                last_signature = signature
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8092)

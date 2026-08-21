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
from .retailer_sources import is_active_retailer


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
        if (
            not isinstance(source, dict)
            or not _publication_is_current(source)
            or not is_active_retailer(str(source.get("retailer") or ""))
        ):
            continue
        fingerprint = str(source.get("fingerprint") or ""5ë¾·¶‰žËkºwµç][ÛœÓ[Ý[ŠNÂˆYˆ
Ü\˜][ÛœÓ[Ý[
HÂˆÜ\˜][ÛœÓ[Ý[š[›™\’SHˆÙXÝ[ÛˆYH›Ü\˜][ÛœÐÛØÚÜ]ˆÛ\ÜÏH›ÜË\ÙXÝ[Ûˆ‚ˆ]ˆÛ\ÜÏH›ÜË\ÙXÝ[Û‹ZXY[™È‚ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ‘’Q•Ð‘U’TÑTÜÜ[Ï’Ý\ˆš\šÙ\ˆ8 %[Hðé™[ÚÏÙ]‚ˆÜ[ˆÛ\ÜÏHœ]ZY][X™[œÚÜš]™X™\ÚÞ]]ÛÛ›ÛÜÜ[‚ˆÙ]‚ˆ]ˆÛ\ÜÏH›ÜËXÛØÚÜ]YÜšY‚ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\[™[ÜËYL™H]ˆYH›ÜÑL‘HÙ]Ø\XÛO‚ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\[™[]ˆÛ\ÜÏH›ÜË]]K\›ÝÈ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ‘UPSTÜÜ[‘][]ÚÙ]Ù]]ˆYH›ÜÑœ™\Ú™\ÜÈˆÛ\ÜÏH›ÜË[\ÝÙ]Ø\XÛO‚ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\[™[]ˆÛ\ÜÏH›ÜË]]K\›ÝÈ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ’“Ð”È	ˆðæTÜÜ[•ÛÜšÙ\œÏÚÙ]Ù]]ˆYH›ÜÒ›ØœÈˆÛ\ÜÏH›ÜË[\ÝÙ]Ø\XÛO‚ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\[™[]ˆÛ\ÜÏH›ÜË]]K\›ÝÈ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ•‘T”ÒSÓÜÜ[‘\Þ[Y[	ˆÙ[™[›™[ÙOÚÙ]Ù]]ˆYH›ÜÔ™[X\ÙHÙ]Ø\XÛO‚ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\[™[]ˆÛ\ÜÏH›ÜË]]K\›ÝÈ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈST“Q“Ô“0æÜÜ[ZÝ]™H[\›Y\ÚÙ]Ù]]ˆYH›ÜÐ[\ÈˆÛ\ÜÏH›ÜË[\ÝÙ]Ø\XÛO‚ˆÙ]‚ˆ]ˆÛ\ÜÏH›ÜË]™[™YÜšYˆYH›ÜÕ™[™ÜšYÙ]‚ˆÜÙXÝ[Û‚ˆÂˆB‚ˆÛÛœÝ[˜UÜH	
ˆÛ[˜H›[˜K]ÜYÜšYŠNÂˆYˆ
[˜UÜ
HÂˆ[˜UÜš[œÙ\Y˜XÙ[S
˜Y\™[™‹ˆ]ˆÛ\ÜÏH›ÜË[[˜KYÜšY‚ˆÙXÝ[ÛˆÛ\ÜÏHœ[™[Ý\™˜XÙH]ˆÛ\ÜÏHœ[™[ZXY\ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ’ÕSUUÔ0áU’T’Ó’S‘ÏÜÜ[Ï’˜Y™]Y\ˆ8 'Û\ˆYY›Ü˜™ZÛ8 'OÏÚÏÙ]Ù]]ˆYH›ÜÑYÜ˜YYÙ]ÜÙXÝ[Û‚ˆÙXÝ[ÛˆÛ\ÜÏHœ[™[Ý\™˜XÙH]ˆÛ\ÜÏHœ[™[ZXY\ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ“SKSÓRÓÔÕ’S‘ÑTÜÜ[Ï‘˜ZÝ\ÚÙHÜ[RKZØ[ÚÏÙ]Ù]]ˆYH›ÜÓÜ[RQ]™[ÈˆÛ\ÜÏH›ÜË[\ÝÙ]ÜÙXÝ[Û‚ˆÙ]‚ˆ
NÂˆB‚ˆÛÛœÝ[YÜ˜][Û‘ÜšYH	
ˆÚ[YÜ˜][Û‘ÜšYŠNÂˆYˆ
[YÜ˜][Û‘ÜšY
HÂˆ[YÜ˜][Û‘ÜšYš[œÙ\Y˜XÙ[S
˜™Y›Ü™X™YÚ[ˆ‹]ˆYH›ÜÒ[YÜ˜][Û”]X[]HˆÛ\ÜÏH›ÜË\]X[]KYÜšYÙ]˜
NÂˆB‚ˆÛÛœÝ]PØ\™ÈH	
ˆÙ]PØ\™ÈŠNÂˆYˆ
]PØ\™ÊHÂˆ]PØ\™Ëš[œÙ\Y˜XÙ[S
˜Y\™[™‹ˆ]ˆÛ\ÜÏH›ÜËY]KYÜšY‚ˆÙXÝ[ÛˆÛ\ÜÏHœ[™[Ý\™˜XÙH]ˆÛ\ÜÏHœ[™[ZXY\ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ‘UTÕS‘QÜÜ[Ï’[YÜš]]ÚÏÙ]Ù]]ˆYH›ÜÒ[YÜš]HˆÛ\ÜÏH›ÜË[\ÝÙ]ÜÙXÝ[Û‚ˆÙXÝ[ÛˆÛ\ÜÏHœ[™[Ý\™˜XÙH]ˆÛ\ÜÏHœ[™[ZXY\ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ”ÒRÒÑT’QÔÕUTÏÜÜ[Ï”ÚZÚÙ\šYÚÏÙ]Ù]]ˆYH›ÜÔÙXÝ\š]HˆÛ\ÜÏH›ÜË[\ÝÙ]ÜÙXÝ[Û‚ˆÙXÝ[ÛˆÛ\ÜÏHœ[™[Ý\™˜XÙH]ˆÛ\ÜÏHœ[™[ZXY\ˆ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ”‘QÒTÕ‘T‘QHS’QTÜÜ[ÏšTÛ™\ÏÚÏÙ]Ù]]ˆYH›ÜÐÛY[ÈˆÛ\ÜÏH›ÜË[\ÝÙ]ÜÙXÝ[Û‚ˆÙ]‚ˆ
NÂˆB‚ˆÛÛœÝXÝ]š]RXY[™ÈH	
ˆØXÝ]š]HœÙXÝ[Û‹ZXY[™ÈŠNÂˆYˆ
XÝ]š]RXY[™ÊHÂˆÛÛœÝ\˜YÜ˜\H	
œ‹XÝ]š]RXY[™ÊNÂˆYˆ
\˜YÜ˜\
H\˜YÜ˜\^ÛÛ[H’Ý[ˆY[š[™ÜÙ[H0é›™[Ù\ŽˆÞ\Ý[\ÚÚY]š\ÜÝ]\ÈÙÈ˜ZÝ\ÚÙH[˜KÓÜ[RKZØ[ˆX\™X]\Û[™ÈÚÚ[\ËˆŽÂˆXÝ]š]RXY[™Ëš[œÙ\Y˜XÙ[S
˜Y\™[™‹ˆ]ˆÛ\ÜÏH›ÜËXXÝ]š]K]ÛÛ˜\ˆ‚ˆ]ˆÛ\ÜÏHœÙYÛY[YXÛÛ›ÛˆYH›ÜÐXÝ]š]Qš[\œÈ‚ˆ]ÛˆÛ\ÜÏHœÙYÛY[\ËXXÝ]™Hˆ]KXXÝ]š]OH˜[[OØ]Û‚ˆ]ÛˆÛ\ÜÏHœÙYÛY[ˆ]KXXÝ]š]OH›[˜H“[˜HÈÜ[ROØ]Û‚ˆ]ÛˆÛ\ÜÏHœÙYÛY[ˆ]KXXÝ]š]OH™›Y\ˆ]š\Ù\Ø]Û‚ˆ]ÛˆÛ\ÜÏHœÙYÛY[ˆ]KXXÝ]š]OHœÞ\Ý[H”Þ\Ý[OØ]Û‚ˆÙ]‚ˆ]ˆYH›ÜÐXÝ]š]TÝ[[X\žHˆÛ\ÜÏHœ]ZY][X™[Ù]‚ˆÙ]‚ˆ
NÂˆ		
ˆÛÜÐXÝ]š]Qš[\œÈœÙYÛY[ŠK™›Ü‘XXÚ

]ÛŠHOˆ]Û‹˜Y]™[\Ý[™\Š˜ÛXÚÈ‹

HOˆÂˆ		
ˆÛÜÐXÝ]š]Qš[\œÈœÙYÛY[ŠK™›Ü‘XXÚ

][JHOˆ][K˜Û\ÜÓ\Ýœ™[[Ý™Jš\ËXXÝ]™HŠJNÂˆ]Û‹˜Û\ÜÓ\Ý˜Y
š\ËXXÝ]™HŠNÂˆXÝ]š]Qš[\ˆH]Û‹™]\Ù]˜XÝ]š]NÂˆYˆ
]\Ý
H™[™\XÝ]š]J]\Ý
NÂˆJJNÂˆBˆB‚ˆ[˜Ý[Ûˆ™[™\‘L‘JÛ˜\ÚÝ
HÂˆÛÛœÝL™HHÛ˜\ÚÝ›Ü\˜][ÛœÏË™[™Ý×Ù[™ßNÂˆÛÛœÝ\™Ù]H	
ˆÛÜÑL‘HŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝX[HHL™K›Ü\˜][Û˜[ÜÝ]\ÈOOHšX[HŽÂˆ\™Ù]š[›™\’SHˆ]ˆÛ\ÜÏH›ÜË]]K\›ÝÈ]Ü[ˆÛ\ÜÏHœ[™[Y^YXœ›ÝÈ‘S‘UËQS‘ÜÜ[‰ÚX[HÈ’Ý\œÈšYÚðé™H\ˆÝ[™ˆˆ‘šYÚðé™[ˆÜ°é™\ˆÜpéœšÜÛÛZYŸOÚÙ]‰Ø˜YÙJL™K›Ü\˜][Û˜[ÜÝ]\ËL™K›Ü\˜][Û˜[ÜÝ]\Ê_OÙ]‚ˆÛ\ÜÏH›ÜËXÛÜH‰Ù\ØÊL™K››ÝHˆŠ_OÜ‚ˆ]ˆÛ\ÜÏH›ÜË\ÝYÙK\Ýš\‰ÊL™KœÝYÙ\È×JK›X\

ÝYÙJHOˆ]ˆÛ\ÜÏH›ÜË\ÝYÙH	ÝÛ™JÝYÙKœÝ]\Ê_HOÚO]Ý›Û™Ï‰Ù\ØÊÝYÙK›˜[YJ_OÜÝ›Û™ÏÜ[‰Ù\ØÊÝYÙK™]Z[ÝYÙKœÝ]\Ê_OÜÜ[Ù]Ù]˜
Kš›Ú[ŠˆŠ_OÙ]‚ˆ]ˆÛ\ÜÏH›ÜË\]X[]K[[™HÜ[’Ý˜[]]ÜÝ]\ÏÜÜ[‰Ø˜YÙJL™Kœ]X[]WÜÝ]\ËL™Kœ]X[]WÜÝ]\Ê_OÙ]‚ˆÂˆB‚ˆ[˜Ý[Ûˆ™[™\‘œ™\Ú™\ÜÊÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÑœ™\Ú™\ÜÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆ\™Ù]š[›™\’SH
Û˜\ÚÝ›Ü\˜][ÛœÏË™œ™\Ú™\ÜÈ×JK›X\

›ÝÊHOˆˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝ	ÝÛ™J›ÝËšX[
_HÜÜ[]Ý›Û™Ï‰Ù\ØÊ›ÝË›˜[YJ_OÜÝ›Û™ÏÛX[‰Ù\ØÊ›ÝË™]Z[
›ÝË˜]ÈÙ[™\Ý	Ù›]]U[YJ›ÝË˜]
_Xˆš[™Ù[ˆ[Y\Ý[\ŠJ_OÜÛX[Ù]‰Ù\ØÊ›]YÙJ›ÝË˜YÙWÜÙXÛÛ™ÊJ_OØÙ]‚ˆ
Kš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[Ûˆ™[™\’›ØœÊÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÒ›ØœÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆ\™Ù]š[›™\’SH
Û˜\ÚÝ›Ü\˜][ÛœÏËš›ØœÈ×JK›X\

›ØŠHOˆÂˆÛÛœÝ›ØÝ\ÈH›Ø‹™›ØÝ\È	‰ˆ\[Ùˆ›Ø‹™›ØÝ\ÈOOH›Øš™XÝˆÈ	Ú›Ø‹™›ØÝ\Ëœ™]Z[\ˆˆŸH	Ú›Ø‹™›ØÝ\Ë]HˆŸXš[J
HˆˆŽÂˆÛÛœÝ™^H›Ø‹›™^Ü[—Ú[—ÚÝ\œÈOH[È°éœÝHÛH	Ó[X™\Š›Ø‹›™^Ü[—Ú[—ÚÝ\œÊKÓØØ[TÝš[™Ê™KQÈ‹ÈX^[][Qœ˜XÝ[Û‘YÚ]ÎˆHJ_H˜ˆˆŽÂˆ™]\›ˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝ	ÝÛ™J›Ø‹šX[
_HÜÜ[]Ý›Û™Ï‰Ù\ØÊ›Ø‹›˜[YJ_OÜÝ›Û™ÏÛX[‰Ù\ØÊ›ØÝ\È™^›Ø‹™]Z[›Ø‹œÝ]HšYHŠ_OÜÛX[Ù]‰Ù\ØÊ›Ø‹œÝ]H¸ %Š_OØÙ]˜ÂˆJKš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[Ûˆ™[™\”™[X\ÙJÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÔ™[X\ÙHŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝ™[X\ÙHHÛ˜\ÚÝ›Ü\˜][ÛœÏË™\Þ[Y[ßNÂˆÛÛœÝ˜XÚÝ\HÛ˜\ÚÝ›Ü\˜][ÛœÏË˜˜XÚÝ\ßNÂˆÛÛœÝšYÛ™HH™[X\ÙK™šYOOH››Û™HˆÈšX[Hˆˆ™[X\ÙK™šYOOH™]XÝYˆÈ™\œ›Üˆˆˆš[™›ÈŽÂˆ\™Ù]š[›™\’SHˆ]ˆÛ\ÜÏH›ÜË\™[X\ÙKYÜšY‚ˆ]Ü[ÛÛ›ÛÙ[\ÜÜ[Ý›Û™Ï‰Ù\ØÊ™[X\ÙK˜ÛÛ›ÛØÙ[\ˆ¸ %Š_OÜÝ›Û™ÏÙ]‚ˆ]Ü[Z[ÛÛ[Z]ÜÜ[Ý›Û™ÈÛ\ÜÏH›[Û›È‰Ù\ØÊÝš[™Ê™[X\ÙK˜Z[ØÛÛ[Z][šÛ›ÝÛˆŠKœÛXÙJL
J_OÜÝ›Û™ÏÙ]‚ˆ]Ü[‘\ÞHX\šÙ\ÜÜ[Ý›Û™ÈÛ\ÜÏH›[Û›È‰Ù\ØÊÝš[™Ê™[X\ÙK›X\šÙ\—ØÛÛ[Z][šÛ›ÝÛˆŠKœÛXÙJL
J_OÜÝ›Û™ÏÙ]‚ˆ]Ü[‘šYÜÜ[‰Ø˜YÙJšYÛ™K™[X\ÙK™šY[šÛ›ÝÛˆŠ_OÙ]‚ˆÙ]‚ˆ]ˆÛ\ÜÏH›ÜËX˜XÚÝ\Ü[”Ù[™\ÝH˜XÚÝ\ÜÜ[Ý›Û™Ï‰Ø˜XÚÝ\›\ÝØ˜XÚÝ\Ø]È\ØÊ›]]U[YJ˜XÚÝ\›\ÝØ˜XÚÝ\Ø]
JHˆ’ZÚÙH™YÚ\Ý™\™][™HŸOÜÝ›Û™ÏÛX[‰Ù\ØÊ˜XÚÝ\››ÝH
˜XÚÝ\˜YÙWÜÙXÛÛ™ÈOH[È	Ù›]YÙJ˜XÚÝ\˜YÙWÜÙXÛÛ™Ê_HØ[[Y[ˆˆŠJ_OÜÛX[Ù]‚ˆÂˆB‚ˆ[˜Ý[Ûˆ™[™\[\Y™XÞXÛJÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÐ[\ÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝ[\ÈHÛ˜\ÚÝ˜[\È×NÂˆYˆ
X[\Ë›[™Ý
HÂˆ\™Ù]š[›™\’SH]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝX[HÜÜ[]Ý›Û™Ï’[™Ù[ˆZÝ]™H[\›Y\ÜÝ›Û™ÏÛX[’[™Ù[ˆYØ[™Ý°éœ™[™H[\›KY\\ÛÙ\ÜÛX[Ù]šX[OØÙ]˜Âˆ™]\›ŽÂˆBˆ\™Ù]š[›™\’SH[\Ë›X\

›ÝÊHOˆˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝ	ÝÛ™J›ÝËœÙ]™\š]J_HÜÜ[]Ý›Û™Ï‰Ù\ØÊ›ÝË]J_OÜÝ›Û™ÏÛX[™°îœÝÙ]	Ù\ØÊ›]]U[YJ›ÝË™š\œÝÜÙY[ŠJ_H0­È	Ù\ØÊ›ÝË™]Z[ˆŠ_OÜÛX[Ù]‰Ù\ØÊ›]YÙJ›ÝË™\˜][Û—ÜÙXÛÛ™ÊJ_H0­È	Ù›][
›ÝË›ØØÝ\œ™[˜Ù\Ê_påÏØÙ]‚ˆ
Kš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[ÛˆÜ\šÛ[™JÙ\šY\ÊHÂˆÛÛœÝ›ÝÜÈH
Ù\šY\È×JK™š[\Š
›ÝÊHOˆ›ÝÈ	‰ˆ›ÝË˜[YHOOH[	‰ˆ›ÝË˜[YHOOH[™Yš[™Y
NÂˆYˆ
›ÝÜË›[™ÝŠH™]\›ˆ]ˆÛ\ÜÏH›ÜË\Ü\šËY[\H”Ø[[\ˆËYYÙ\È\ÝÜšZø )Ù]˜ÂˆÛÛœÝ˜[Y\ÈH›ÝÜË›X\

›ÝÊHOˆ[X™\Š›ÝË˜[YJJNÂˆÛÛœÝZ[ˆHX]›Z[Š‹‹˜[Y\ÊNÂˆÛÛœÝX^HX]›X^
‹‹˜[Y\ÊNÂˆÛÛœÝÜ[ˆHX^HZ[ˆNÂˆÛÛœÝÚ[ÈH˜[Y\Ë›X\

˜[YK[™^
HOˆ	Ê[™^È
˜[Y\Ë›[™ÝHJH
ˆL
KÑš^Y
Š_K	ÊŽH

˜[YHHZ[ŠHÈÜ[ˆ
ˆ
JKÑš^Y
Š_X
Kš›Ú[ŠˆŠNÂˆ™]\›ˆÝ™ÈÛ\ÜÏH›ÜË\Ü\šÈˆšY]Ð›ÞHŒLÌˆˆ™\Ù\™P\ÜXÝ˜][ÏH››Û™Hˆ\šXKZY[HYHÛ[[™HÚ[ÏH‰ÜÚ[ßHˆ™XÝÜ‹YY™™XÝH››Û‹\ØØ[[™Ë\Ý›ÚÙHÜÛ[[™OÜÝ™Ï˜ÂˆB‚ˆ[˜Ý[Ûˆ™[™\•™[™ÊÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÕ™[™ÜšYŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝÙ\šY\ÈHÛ˜\ÚÝ›Ü\˜][ÛœÏË™[™ÏËœÙ\šY\ÈßNÂˆÛÛœÝYš[š][ÛœÈHÂˆÈ˜ÛÜ™WÛ\È‹ÛÜ™H][˜ÞH‹›\È—KˆÈ›[Øš[WÛ\È‹“[Øš[H][˜ÞH‹›\È—KˆÈ›[˜WØÛÜÝÙÚÈ‹“[˜HÜ[™‹šÜ‹ˆ—KˆÈ˜ÛÝ™\˜YÙWÙYÜ˜YY‹‘YÜ˜YY‹˜]š\Ù\ˆ—KˆNÂˆ\™Ù]š[›™\’SHYš[š][ÛœË›X\

ÚÙ^KX™[[š]JHOˆÂˆÛÛœÝ›ÝÜÈHÙ\šY\ÖÚÙ^WH×NÂˆÛÛœÝÝ\œ™[H›ÝÜË˜]
LJNÂˆÛÛœÝš\œÝH›ÝÜÖÌNÂˆÛÛœÝ[HHÝ\œ™[	‰ˆš\œÝÈ[X™\ŠÝ\œ™[˜[YJHH[X™\Šš\œÝ˜[YJHˆ[Âˆ™]\›ˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË]™[™XØ\™]Ü[‰Ù\ØÊX™[
_OÜÜ[Ý›Û™Ï‰ØÝ\œ™[È	Ó[X™\ŠÝ\œ™[˜[YJKÓØØ[TÝš[™Ê™KQÈ‹ÈX^[][Qœ˜XÝ[Û‘YÚ]ÎˆˆJ_H	Ý[š]Xˆ¸ %ŸOÜÝ›Û™ÏÛX[‰Ù[HOH[Èš[™Ù[ˆ™[™[™Hˆˆ	Ù[HHÈŠÈˆˆˆŸIÙ[KÓØØ[TÝš[™Ê™KQÈ‹ÈX^[][Qœ˜XÝ[Û‘YÚ]ÎˆˆJ_HÝ™\ˆ\ÝÜšZÚÙ[˜OÜÛX[Ù]‰ÜÜ\šÛ[™J›ÝÜÊ_OØ\XÛO˜ÂˆJKš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[Ûˆ™[™\‘YÜ˜YY
Û˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÑYÜ˜YYŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝ]HHÛ˜\ÚÝ›Ü\˜][ÛœÏË™YÜ˜YYÚ[\XÝßNÂˆ\™Ù]š[›™\’SHˆ]ˆÛ\ÜÏH›ÜËZ[\XÝYÜšY‚ˆ]Ý›Û™Ï‰Ù›][
]K™YÜ˜YYÜX›XØ][ÛœÊ_OÜÝ›Û™ÏÜ[™YÜ˜YYÜÜ[Ù]‚ˆ]Ý›Û™Ï‰Ù›][
]K˜Ý\ÝÛY\—ÜÙ[œÚ]]™WÜX›XØ][ÛœÊ_OÜÝ›Û™ÏÜ[œÝ[Y[š\ËÛY[X™\‹\Ù[œÚ]]™OÜÜ[Ù]‚ˆ]Ý›Û™Ï‰Ù›][
]K›Ý\—Ü]X[]WÜX›XØ][ÛœÊ_OÜÝ›Û™ÏÜ[°îœšYÈÝ˜[]]ÜÜ[Ù]‚ˆÙ]‚ˆÛ\ÜÏH›ÜËXÛÜH‰Ù\ØÊ]K››ÝHˆŠ_OÜ‚ˆ]ˆÛ\ÜÏH›ÜË\™X\ÛÛœÈ‰Ê]KÜÜ™X\ÛÛœÈ×JK›[™ÝÈ]KÜÜ™X\ÛÛœË›X\

›ÝÊHOˆ]Ü[‰Ù\ØÊ›ÝËœ™X\ÛÛŠ_OÜÜ[Ý›Û™Ï‰Ù›][
›ÝË˜ÛÝ[
_OÜÝ›Û™ÏÙ]˜
Kš›Ú[ŠˆŠHˆ	ÏÜ[ˆÛ\ÜÏH™[\K\Ý]HÛÛ\XÝ’[™Ù[ˆZÝY[H]X\˜[[™Kpé\œØYÙ\‹ÜÜ[‰ßOÙ]‚ˆÂˆB‚ˆ[˜Ý[Ûˆ™[™\“Ü[RQ]™[ÊÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÓÜ[RQ]™[ÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝ›ÝÜÈH
Û˜\ÚÝ[[Y]žOË[Y[[™H×JK™š[\Š
›ÝÊHOˆ›ÝË˜Ø]YÛÜžHOOH›[˜Hˆ	‰ˆ›ÝË\HOOH›Ü[˜ZWÝ\ØYÙHŠKœÛXÙJŠNÂˆ\™Ù]š[›™\’SH›ÝÜË›[™ÝˆÈ›ÝÜË›X\

›ÝÊHOˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝÛÜÝÜÜ[]Ý›Û™Ï‰Ù\ØÊ›ÝË]J_OÜÝ›Û™ÏÛX[‰Ù\ØÊ›ÝË™]Z[ˆŠ_OÜÛX[Ù]‰Ü›ÝË˜ÛÜÝÙÚÈOH[È
ÉÙ›]ÚÊ›ÝË˜ÛÜÝÙÚË
_HÜ‹˜ˆ¸ %ŸOØÙ]˜
Kš›Ú[ŠˆŠBˆˆ	Ï]ˆÛ\ÜÏH™[\K\Ý]HÛÛ\XÝ’[™Ù[ˆžYHÜ[RKZØ[™YÚ\Ý™\™]ÚY[ˆÜ\˜][ÛœÈŒˆ›]ˆZÝ]™\™]Ù]‰ÎÂˆB‚ˆ[˜Ý[Ûˆ™[™\’[YÜ˜][Û”]X[]JÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÒ[YÜ˜][Û”]X[]HŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆ\™Ù]š[›™\’SH
Û˜\ÚÝ›Ü\˜][ÛœÏËš[YÜ˜][Û—Ü]X[]H×JK›X\

›ÝÊHOˆˆ\XÛHÛ\ÜÏHœÝ\™˜XÙHÜË\]X[]KXØ\™]ˆÛ\ÜÏH›ÜË]]K\›ÝÈÝ›Û™Ï‰Ù\ØÊ›ÝË›˜[YJ_OÜÝ›Û™Ï‰Ø˜YÙJ›ÝËšX[›ÝËœÝ]J_OÙ]]ˆÛ\ÜÏH›ÜË\]X[]K[Y]šXÜÈÜ[”ÚYÝHÝXØÙ\È‰Ù\ØÊ›]]U[YJ›ÝË›\ÝÜÝXØÙ\Ü×Ø]
J_OØÜÜ[‰Ü›ÝË›][˜ÞWÛ\ÈOH[ÈÜ[“][˜ÞH‰Ù›][
›ÝË›][˜ÞWÛ\Ê_H\ÏØÜÜ[˜ˆˆŸOÙ]‰Ù\ØÊ›ÝË™]Z[ˆŠ_OÜØ\XÛO‚ˆ
Kš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[Ûˆ™[™\‘]JÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÙ]PØ\™ÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝÝÜ˜YÙHHÛ˜\ÚÝ™]OËœÝÜ˜YÙHßNÂˆÛÛœÝ\˜Ú]™HHÛ˜\ÚÝ™›Y\œÏË˜\˜Ú]™HßNÂˆÛÛœÝÝ\ÙZÛÈHÛ˜\ÚÝ™]OËšÝ\ÙZÛÈßNÂˆÛÛœÝY]Y]HHÛ˜\ÚÝ™]OË›Ù™™\—ÛY]Y]HßNÂˆÛÛœÝY[]HHÛ˜\ÚÝ™]OËœ›ÙXÝÚY[]HßNÂˆ\™Ù]š[›™\’SHˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜË\ÝÜ˜YÙK\š[X\žHÜ[’Ý\ˆ\œÚ\Ý[]OÜÜ[Ý›Û™Ï‰Ù›]ž]\ÊÝÜ˜YÙKšÝ\—Ü\œÚ\Ý[Øž]\Ê_OÜÝ›Û™ÏÛX[’Ý[ˆÝ\ˆÙ]H8 %ZÚÙH™\Ý[ˆYˆSTÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[]š\Ø\šÚ]ÜÜ[Ý›Û™Ï‰Ù›]ž]\ÊÝÜ˜YÙK™›Y\—Ú\ÝÜžWØž]\Ê_OÜÝ›Û™ÏÛX[‰Ù›][
\˜Ú]™Kœ™]Z[™YÙÙ[™\˜][ÛœÊ_HÙ[™\˜][Û™\ˆÙ[]0­È	Ù›][
\˜Ú]™K˜Ý\œ™[ØXÝ]™WÙÙ[™\˜][ÛœÊ_HZÝY[H0­Èš[Y\ˆÙ[[Y\ÈZÚÙHÚØ[ÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[°æœšYÙHÝ\‹Y]OÜÜ[Ý›Û™Ï‰Ù›]ž]\ÊÝÜ˜YÙK›Ý\—ÚÝ\—Øž]\Ê_OÜÝ›Û™ÏÛX[‘˜[Z[Y\‹\Ý\‹Y]Y]K\ÚÙÈ0îœšYÈ\œÚ\Ý[[Ý[™ÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[”STYYÈYÏÜÜ[Ý›Û™Ï‰Ù›]ž]\ÊÝÜ˜YÙKœ[˜\Ý›Û[YWÙœ™YWØž]\Ê_OÜÝ›Û™ÏÛX[’[HST]›Û[YH0­ÈÝ[	Ù›]ž]\ÊÝÜ˜YÙKœ[˜\Ý›Û[YWÝÝ[Øž]\Ê_H0­ÈÜÝœYÝ	Ù›]ž]\ÊÝÜ˜YÙKœ[˜\Ý›Û[YWÝ\ÙYØž]\Ê_OÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[‘˜[Z[YY]OÜÜ[Ý›Û™Ï‰Ù›][
Ý\ÙZÛË›Y[X™\œÊ_OÜÝ›Û™ÏÛX[‰Ù›][
Ý\ÙZÛËšÝ\ÙZÛÊ_H˜[Z[Y\ˆ0­È	Ù›][
Ý\ÙZÛËœ[™[™×Ú[š]\Ê_HZÝ]™H[š]\ÏÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[•[YÛY]Y]OÜÜ[Ý›Û™Ï‰Ù›][
Y]Y]Kœ™XÛÜ™Ê_OÜÝ›Û™ÏÛX[‰Ù›][
Y]Y]Kœ[›™Y
_H[›™Y0­È	Ù›][
Y]Y]KÚ]ÛÙ™™\—ÜÛ˜\ÚÝ
_HÛ˜\ÚÝÏÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[’Y[]K[0éœš[™ÏÜÜ[Ý›Û™Ï‰Ù›][
Y[]KœÝÜ™YÜ[\Ê_OÜÝ›Û™ÏÛX[œ\œÚ\Ý[›ÙXÝY[]HÝ]OÜÛX[Ø\XÛO‚ˆ\XÛHÛ\ÜÏH™]K\Ý]XØ\™Ý\™˜XÙHÜ[ÛÛ›ÛÙ[\ˆ\ÝÜšZÏÜÜ[Ý›Û™Ï‰Ù›]ž]\ÊÝÜ˜YÙK˜ÛÛ›ÛØÙ[\—Ý[[Y]žWØž]\Ê_OÜÝ›Û™ÏÛX[™]™[Ë™[™ÈÙÈ[\[Y™XÞXÛOÜÛX[Ø\XÛO‚ˆÂˆB‚ˆ[˜Ý[Ûˆ™[™\“\Ý
\™Ù]Ù[XÝÜ‹›ÝÜÊHÂˆÛÛœÝ\™Ù]H	
\™Ù]Ù[XÝÜŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆ\™Ù]š[›™\’SH›ÝÜË›X\

›ÝÊHOˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝ	ÝÛ™J›ÝËœÝ]\Ê_HÜÜ[]Ý›Û™Ï‰Ù\ØÊ›ÝË›˜[YJ_OÜÝ›Û™ÏÛX[‰Ù\ØÊ›ÝË™]Z[ˆŠ_OÜÛX[Ù]‰Ù\ØÊ›ÝËœÝ]\È¸ %Š_OØÙ]˜
Kš›Ú[ŠˆŠNÂˆB‚ˆ[˜Ý[Ûˆ™[™\’[YÜš]JÛ˜\ÚÝ
HÂˆ™[™\“\Ý
ˆÛÜÒ[YÜš]H‹Û˜\ÚÝ™]OËš[YÜš]OË˜ÚXÚÜÈ×JNÂˆB‚ˆ[˜Ý[Ûˆ™[™\”ÙXÝ\š]JÛ˜\ÚÝ
HÂˆ™[™\“\Ý
ˆÛÜÔÙXÝ\š]H‹Û˜\ÚÝ›Ü\˜][ÛœÏËœÙXÝ\š]H×JNÂˆB‚ˆ[˜Ý[Ûˆ™[™\ÛY[ÊÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÛÜÐÛY[ÈŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝÛY[ÈHÛ˜\ÚÝ›Ü\˜][ÛœÏË˜ÛY[ÈßNÂˆÛÛœÝ›ÝÜÈHÛY[Ë˜ÛY[È×NÂˆ\™Ù]š[›™\’SHˆ]ˆÛ\ÜÏH›ÜËXÛY[\Ý[[X\žHÝ›Û™Ï‰Ù›][
ÛY[Ë™[˜X›Y
_KÉÙ›][
ÛY[Ëœ™YÚ\Ý\™Y
_OÜÝ›Û™ÏÜ[œ\ÚXZÝ]™HÛY[\ÜÜ[Ù]‚ˆ	Ü›ÝÜË›[™ÝÈ›ÝÜË›X\

›ÝÊHOˆ]ˆÛ\ÜÏH›ÜË\›ÝÈÜ[ˆÛ\ÜÏH›ÜË\Ý]KYÝ	Ü›ÝË™[˜X›YÈšX[Hˆˆ˜][[ÛˆŸHÜÜ[]Ý›Û™Ï‰Ù\ØÊ›ÝË›X™[
_OÜÝ›Û™ÏÛX[‰Ù\ØÊÜ›ÝË™\œÚ[Ûˆ	‰ˆ‰Ü›ÝË™\œÚ[ÛŸX›ÝË˜Z[	‰ˆZ[	Ü›ÝË˜Z[X›ÝË™[š\›Û›Y[K™š[\Š›ÛÛX[ŠKš›Ú[Šˆ0­ÈŠHTœÈ™YÚ\Ý˜][ÛˆŠ_OÜÛX[Ù]‰Ù\ØÊ›ÝËœ\ÚÜ\›Z\ÜÚ[Ûˆ
›ÝË™[˜X›YÈ™[˜X›Yˆˆ™\ØX›YŠJ_OØÙ]˜
Kš›Ú[ŠˆŠHˆ	Ï]ˆÛ\ÜÏH™[\K\Ý]HÛÛ\XÝ’[™Ù[ˆTœËZÛY[\ˆ™YÚ\Ý™\™]Ù]‰ßBˆÛ\ÜÏH›ÜË[ZXÜ›ØÛÜH‰Ù\ØÊÛY[Ë››ÝHˆŠ_OÜ‚ˆÂˆB‚ˆ[˜Ý[Ûˆ™[™\‘\[™[˜ÞRX[
Û˜\ÚÝ
HÂˆÛÛœÝYÙRX[H™]ÈX\

Û˜\ÚÝ›Ü\˜][ÛœÏË™\[™[˜ÞWÛX\Ë™YÙ\È×JK›X\

YÙJHOˆØ	ÙYÙK™œ›Û_O‰ÙYÙKßXYÙKšX[JJNÂˆÛÛœÝ›Ù\ÈH		
ˆÙ]Y›ÝÈ™›ÝË[›ÙHŠNÂˆ›Ù\Ë™›Ü‘XXÚ

›ÙK[™^
HOˆÂˆÛÛœÝ™^H›Ù\ÖÚ[™^
ÈWNÂˆÛÛœÝX[H™^ÈYÙRX[™Ù]
	Û›ÙK™]\Ù]˜ÛÛ\Û™[O‰Û™^™]\Ù]˜ÛÛ\Û™[X
Hˆ[Âˆ›ÙK˜Û\ÜÓ\Ýœ™[[Ý™J›ÜËYYÙKZX[H‹›ÜËYYÙKX][[Ûˆ‹›ÜËYYÙKY\œ›ÜˆŠNÂˆYˆ
X[
H›ÙK˜Û\ÜÓ\Ý˜Y
ÜËYYÙKIÝÛ™JX[
_X
NÂˆJNÂˆB‚ˆ[˜Ý[Ûˆ™[™\XÝ]š]JÛ˜\ÚÝ
HÂˆÛÛœÝ\™Ù]H	
ˆÝ[Y[[™HŠNÂˆYˆ
]\™Ù]
H™]\›ŽÂˆÛÛœÝ[HÛ˜\ÚÝ[[Y]žOË[Y[[™H×NÂˆÛÛœÝ›ÝÜÈHXÝ]š]Qš[\ˆOOH˜[ˆÈ[ˆ[™š[\Š
›ÝÊHOˆ›ÝË˜Ø]YÛÜžHOOHXÝ]š]Qš[\ŠNÂˆÛÛœÝÛÝ[ÈHÛ˜\ÚÝ[[Y]žOË˜XÝ]š]WØØ]YÛÜšY\ÈßNÂˆÛÛœÝÝ[[X\žHH	
ˆÛÜÐXÝ]š]TÝ[[X\žHŠNÂˆYˆ
Ý[[X\žJHÝ[[X\žK^ÛÛ[H	ØÛÝ[Ë›[˜HH[˜H0­È	ØÛÝ[Ë™›Y\ˆH]š\È0­È	ØÛÝ[ËœÞ\Ý[HHÞ\Ý[XÂˆ\™Ù]š[›™\’SH›ÝÜË›[™ÝÈ›ÝÜË›X\

›ÝÊHOˆÂˆÛÛœÝÛÜÝH›ÝË˜ÛÜÝÙÚÈOH[ÈÜ[ˆÛ\ÜÏH›ÜËY]™[XÛÜÝŠÉÙ›]ÚÊ›ÝË˜ÛÜÝÙÚË
_HÜ‹ÜÜ[˜ˆˆŽÂˆÛÛœÝ™\]Y\ÝÈH›ÝËœ™\]Y\ÝÈOH[ÈÜ[‰Ù›][
›ÝËœ™\]Y\ÝÊ_HØ[ÜÜ[˜ˆˆŽÂˆ™]\›ˆ]ˆÛ\ÜÏH›ÜËY]™[\›ÝÈÜ[ˆÛ\ÜÏH[Y[[™K][YH‰Ù\ØÊ›]]U[YJ›ÝË˜]
J_OÜÜ[HÛ\ÜÏH›ÜËY]™[YÝ	ÝÛ™J›ÝËœÙ]™\š]H›ÝËœÝ]\Ê_HÚO]ˆÛ\ÜÏH›ÜËY]™[[XZ[ˆÝ›Û™Ï‰Ù\ØÊ›ÝË]H›ÝË™]Z[›ÝË\J_OÜÝ›Û™ÏÜ[‰Ù\ØÊ›ÝË™]Z[›ÝËœ™]Z[\ˆ›ÝË˜Ø]YÛÜžHˆŠ_OÜÜ[Ù]]ˆÛ\ÜÏH›ÜËY]™[[Y]H‰Ü™\]Y\ÝßIØÛÜÝOÜ[ˆÛ\ÜÏH›ÜËXØ]YÛÜžH‰Ù\ØÊ›ÝË˜Ø]YÛÜžH™]™[Š_OÜÜ[Ù]Ù]˜ÂˆJKš›Ú[ŠˆŠHˆ	Ï]ˆÛ\ÜÏH™[\K\Ý]H’[™Ù[ˆY[š[™ÜÙ[H]™[ÈH]Hš[\‹Ù]‰ÎÂˆB‚ˆ[˜Ý[Ûˆ™[™\ŠÛ˜\ÚÝ
HÂˆ]\ÝHÛ˜\ÚÝÂˆ™[™\‘L‘JÛ˜\ÚÝ
NÂˆ™[™\‘œ™\Ú™\ÜÊÛ˜\ÚÝ
NÂˆ™[™\’›ØœÊÛ˜\ÚÝ
NÂˆ™[™\”™[X\ÙJÛ˜\ÚÝ
NÂˆ™[™\[\Y™XÞXÛJÛ˜\ÚÝ
NÂˆ™[™\•™[™ÊÛ˜\ÚÝ
NÂˆ™[™\‘YÜ˜YY
Û˜\ÚÝ
NÂˆ™[™\“Ü[RQ]™[ÊÛ˜\ÚÝ
NÂˆ™[™\’[YÜ˜][Û”]X[]JÛ˜\ÚÝ
NÂˆ™[™\‘]JÛ˜\ÚÝ
NÂˆ™[™\’[YÜš]JÛ˜\ÚÝ
NÂˆ™[™\”ÙXÝ\š]JÛ˜\ÚÝ
NÂˆ™[™\ÛY[ÊÛ˜\ÚÝ
NÂˆ™[™\‘\[™[˜ÞRX[
Û˜\ÚÝ
NÂˆ™[™\XÝ]š]JÛ˜\ÚÝ
NÂˆB‚ˆØÝ[Y[˜Y]™[\Ý[™\Š‘ÓPÛÛ[ØYY‹

HOˆÂˆ[š™XÝ^[Ý]

NÂˆÚ[™ÝË˜Y]™[\Ý[™\ŠšÝ\ŽœÛ˜\ÚÝ‹
]™[
HOˆ™[™\Š]™[™]Z[ßJJNÂˆJNÂŸJJ
NÂ
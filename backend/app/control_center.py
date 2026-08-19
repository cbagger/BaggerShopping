from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import control_center_snapshot as control_center_snapshot_base
from . import control_center_snapshot_v2
from .control_center_catalog import IOS_RELEASE, catalog, dataflow
from .control_telemetry import read_heartbeat


APP_VERSION = "1.1.0"
STATIC_DIR = Path(__file__).with_name("control_center_static")
SNAPSHOT_TTL_SECONDS = 2.0

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

# Control Center intentionally runs without the production .env. Most Luna state
# is already available from the shared read-only state files; the one fact that
# cannot be inferred safely without the secret itself is whether the worker has
# an OpenAI API key. The Luna worker therefore publishes only a boolean plus the
# public model/config state in its sanitized heartbeat.
_base_luna_status_payload = control_center_snapshot_base.luna_enrichment.status_payload


def _sanitized_luna_status_payload() -> dict[str, Any]:
    payload = dict(_base_luna_status_payload())
    heartbeat = read_heartbeat("luna-worker", stale_after=75)
    metrics = heartbeat.get("metrics") if isinstance(heartbeat.get("metrics"), dict) else {}
    for key in ("enabled", "apply_results", "model", "api_key_configured"):
        if key in metrics:
            payload[key] = metrics[key]
    if isinstance(metrics.get("usage"), dict):
        payload["usage"] = dict(metrics["usage"])
    if isinstance(metrics.get("records"), dict):
        payload["records"] = dict(metrics["records"])
    return payload


control_center_snapshot_base.luna_enrichment.status_payload = _sanitized_luna_status_payload

# Mobile API deliberately disables FastAPI docs/openapi. Its internal `/docs`
# route therefore returns 404 even when the process is perfectly healthy. The
# Control Center has no MOBILE_API_TOKEN by design, so its cheap liveness probe
# must interpret this exact internal 404 as proof that the Mobile API process is
# alive. Production readiness is still independently checked by Mobile API's
# authenticated Docker healthcheck.
_base_runtime_probes = control_center_snapshot_base.runtime_probes


async def _safe_runtime_probes(*, force: bool = False) -> dict[str, dict[str, Any]]:
    probes = await _base_runtime_probes(force=force)
    mobile = probes.get("mobile-api")
    if isinstance(mobile, dict) and mobile.get("status_code") == 404:
        fixed = dict(mobile)
        fixed.update(
            {
                "ok": True,
                "health": "healthy",
                "state": "online",
                "error": None,
                "liveness_evidence": "expected-disabled-docs-404",
            }
        )
        probes = dict(probes)
        probes["mobile-api"] = fixed
    return probes


control_center_snapshot_base.runtime_probes = _safe_runtime_probes


def _private_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def _client_is_local(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().casefold()
    if normalized in {"localhost", "testclient"}:
        return True
    return _private_address(normalized)


def _request_host_is_local(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().casefold().rstrip(".")
    if normalized in {"localhost", "testserver"} or normalized.endswith(".local"):
        return True
    return _private_address(normalized)


@app.middleware("http")
async def local_only(request: Request, call_next):
    client_host = request.client.host if request.client else None
    request_host = request.url.hostname
    if not _client_is_local(client_host) or not _request_host_is_local(request_host):
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


def _index_html() -> str:
    html = (STATIC_DIR / "index.html").read_text("utf-8")
    html = html.replace(
        "</head>",
        '  <link rel="stylesheet" href="/assets/operations.css">\n'
        '  <script src="/assets/operations.js" defer></script>\n'
        "</head>",
    )
    return html


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_index_html())


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "kurv-control-center",
        "version": APP_VERSION,
        "local_only": True,
        "read_only": True,
        "secrets_in_process": False,
        "operations_v2": True,
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
        value = await control_center_snapshot_v2.build_snapshot(control_center_version=APP_VERSION)
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
        while True:
            if await request.is_disconnected():
                break
            payload = await snapshot()
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            yield f"event: snapshot\ndata: {data}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8092)

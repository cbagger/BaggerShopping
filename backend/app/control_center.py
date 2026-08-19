from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import control_center_snapshot
from .control_center_catalog import IOS_RELEASE, catalog, dataflow


APP_VERSION = "1.0.0"
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
    return {
        "ok": True,
        "service": "kurv-control-center",
        "version": APP_VERSION,
        "local_only": True,
        "read_only": True,
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
        value = await control_center_snapshot.build_snapshot(control_center_version=APP_VERSION)
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

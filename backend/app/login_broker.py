from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import BrowserContext, Playwright, async_playwright

MOBILE_API = os.getenv("MOBILE_API_INTERNAL_BASE", "http://mobile-api:8081").rstrip("/")
BROKER_KEY = os.getenv("SAMSUNG_LOGIN_BROKER_KEY", "")
SESSION_TIMEOUT = 15 * 60
NOVNC_ROOT = Path(os.getenv("NOVNC_ROOT", "/usr/share/novnc"))
TOKEN_COOKIE = "kurv_samsung_login"


@dataclass
class ActiveLogin:
    session_id: str
    household_id: str
    secret: str
    expires_at: int
    auth_state: Path
    context: BrowserContext
    discovered_lists: dict[str, str]


app = FastAPI(title="Kurv Samsung Login Broker", docs_url=None, redoc_url=None, openapi_url=None)
if NOVNC_ROOT.exists():
    app.mount("/novnc", StaticFiles(directory=NOVNC_ROOT), name="novnc")

LOCK = asyncio.Lock()
ACTIVE: ActiveLogin | None = None
PLAYWRIGHT: Playwright | None = None


@app.on_event("startup")
async def startup() -> None:
    global PLAYWRIGHT
    if not BROKER_KEY:
        raise RuntimeError("SAMSUNG_LOGIN_BROKER_KEY mangler")
    PLAYWRIGHT = await async_playwright().start()


@app.on_event("shutdown")
async def shutdown() -> None:
    global ACTIVE, PLAYWRIGHT
    if ACTIVE:
        await ACTIVE.context.close()
        ACTIVE = None
    if PLAYWRIGHT:
        await PLAYWRIGHT.stop()
        PLAYWRIGHT = None


async def _claim(login_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{MOBILE_API}/api/mobile/v1/integrations/samsung-food/broker/session/{login_token}"
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Login-linket er brugt eller udløbet")
    return response.json()


def _current(request: Request, session_id: str) -> ActiveLogin:
    if not ACTIVE or ACTIVE.session_id != session_id or ACTIVE.expires_at <= int(time.time()):
        raise HTTPException(status_code=404, detail="Login-sessionen er udløbet")
    supplied = request.cookies.get(TOKEN_COOKIE, "")
    if not supplied or not secrets.compare_digest(supplied, ACTIVE.secret):
        raise HTTPException(status_code=401, detail="Login-sessionen tilhører ikke denne browser")
    return ACTIVE


def _cookie_secure_for_public_url(value: str | None = None) -> bool:
    """Keep cookies Secure except for explicit private-LAN HTTP broker URLs."""

    raw = (value if value is not None else os.getenv("SAMSUNG_LOGIN_PUBLIC_BASE_URL", "")).strip()
    parsed = urlparse(raw)
    if parsed.scheme.casefold() == "https":
        return True
    if parsed.scheme.casefold() != "http" or not parsed.hostname:
        return True
    if parsed.hostname.casefold() == "localhost":
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local)


def _page(session_id: str) -> str:
    safe_id = html.escape(session_id, quote=True)
    return f"""<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Kurv · Samsung Food</title><style>
html,body{{margin:0;height:100%;background:#071b12;color:#fff;font-family:-apple-system,BlinkMacSystemFont,sans-serif;overflow:hidden}}
body{{display:flex;flex-direction:column}}
header{{display:flex;gap:8px;align-items:center;padding:10px 12px calc(10px + env(safe-area-inset-top,0px));background:#0d3423;flex-wrap:wrap;z-index:3}}
header strong{{font-size:16px;line-height:1.15}}#status{{font-size:13px;color:#cce8da;flex:1;min-width:145px}}
button{{border:0;border-radius:12px;padding:10px 13px;font-weight:700;background:#fff;color:#0d3423;font-size:15px}}
#keyboardToggle{{background:#d9f4e6}}#done{{margin-left:auto}}
#keyboardPanel{{display:none;gap:8px;align-items:center;width:100%;padding-top:2px}}
#keyboardPanel.open{{display:flex}}
#keyboardInput{{min-width:0;flex:1;border:1px solid #6d9b84;border-radius:12px;padding:10px 12px;font-size:16px;background:#fff;color:#111;outline:none}}
#keyboardPanel button{{padding:10px 11px;white-space:nowrap}}
#screen{{flex:1;min-height:0;background:#222;position:relative;z-index:1}}
</style></head><body><header><strong>Kurv · Samsung Food-login</strong><span id="status">Logger ind direkte hos Samsung</span>
<button id="keyboardToggle" type="button">⌨︎ Tastatur</button><button id="done" type="button">Jeg er færdig</button>
<div id="keyboardPanel"><input id="keyboardInput" type="text" autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false" placeholder="Skriv her …">
<button id="sendText" type="button">Send</button><button id="backspace" type="button">⌫</button><button id="tabKey" type="button">Tab</button><button id="enterKey" type="button">↵</button></div>
</header><div id="screen"></div>
<script type="module">import RFB from '/novnc/core/rfb.js';
const scheme=location.protocol==='https:'?'wss':'ws';
const rfb=new RFB(document.getElementById('screen'),`${{scheme}}://${{location.host}}/ws/{safe_id}`);
rfb.scaleViewport=true;rfb.resizeSession=true;
const keyboardToggle=document.getElementById('keyboardToggle');
const keyboardPanel=document.getElementById('keyboardPanel');
const keyboardInput=document.getElementById('keyboardInput');
const sendTextButton=document.getElementById('sendText');
const status=document.getElementById('status');
function keysymForCharacter(char){{
 const codepoint=char.codePointAt(0);
 return codepoint<=0xff?codepoint:(0x01000000|codepoint);
}}
function sendText(){{
 const value=keyboardInput.value;
 if(!value)return;
 rfb.focus();
 for(const char of value)rfb.sendKey(keysymForCharacter(char),null);
 keyboardInput.value='';
 status.textContent='Tekst sendt til Samsung';
 keyboardInput.focus();
}}
keyboardToggle.onclick=()=>{{
 const opening=!keyboardPanel.classList.contains('open');
 keyboardPanel.classList.toggle('open',opening);
 keyboardToggle.textContent=opening?'Skjul tastatur':'⌨︎ Tastatur';
 if(opening)setTimeout(()=>keyboardInput.focus(),50);
}};
sendTextButton.onclick=sendText;
keyboardInput.addEventListener('keydown',event=>{{
 if(event.key==='Enter'){{event.preventDefault();sendText();rfb.sendKey(0xff0d,'Enter');}}
}});
document.getElementById('backspace').onclick=()=>{{rfb.focus();rfb.sendKey(0xff08,'Backspace');keyboardInput.focus();}};
document.getElementById('tabKey').onclick=()=>{{rfb.focus();rfb.sendKey(0xff09,'Tab');keyboardInput.focus();}};
document.getElementById('enterKey').onclick=()=>{{sendText();rfb.focus();rfb.sendKey(0xff0d,'Enter');keyboardInput.focus();}};
document.getElementById('done').onclick=async()=>{{
 const b=document.getElementById('done');b.disabled=true;status.textContent='Kontrollerer Samsung-session og lister …';
 const r=await fetch('/session/{safe_id}/complete',{{method:'POST'}});const j=await r.json().catch(()=>({{}}));
 status.textContent=r.ok?'Login færdigt – gå tilbage til Kurv':(j.detail||'Login kunne ikke færdiggøres');
 if(!r.ok)b.disabled=false;
}};</script></body></html>"""


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "active_session": ACTIVE.session_id if ACTIVE else None}


@app.get("/session/{login_token}", response_class=HTMLResponse)
async def open_session(login_token: str) -> HTMLResponse:
    global ACTIVE
    async with LOCK:
        if ACTIVE and ACTIVE.expires_at > int(time.time()):
            raise HTTPException(status_code=409, detail="En anden familie er ved at logge ind. Prøv igen om få minutter.")
        if ACTIVE:
            await ACTIVE.context.close()
            ACTIVE = None
        claim = await _claim(login_token)
        profile = Path(claim["browser_profile"])
        auth_state = Path(claim["auth_state"])
        profile.mkdir(parents=True, exist_ok=True)
        auth_state.parent.mkdir(parents=True, exist_ok=True)
        if PLAYWRIGHT is None:
            raise HTTPException(status_code=503, detail="Browseren er ikke klar")
        context = await PLAYWRIGHT.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=False,
            viewport={"width": 1280, "height": 820},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--start-maximized"],
        )
        secret = secrets.token_urlsafe(32)
        ACTIVE = ActiveLogin(
            session_id=claim["session_id"], household_id=claim["household_id"],
            secret=secret, expires_at=claim["expires_at"], auth_state=auth_state, context=context,
            discovered_lists={},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def inspect_response(response) -> None:
            if "list" not in response.url.casefold():
                return
            try:
                payload = await response.json()
            except Exception:
                return

            def walk(value) -> None:
                if isinstance(value, dict):
                    identifier = value.get("id") or value.get("list_id")
                    name = value.get("name") or value.get("title")
                    if isinstance(identifier, str) and len(identifier) >= 6 and isinstance(name, str) and name.strip():
                        ACTIVE and ACTIVE.discovered_lists.setdefault(identifier, name.strip()[:200])
                    for nested in value.values():
                        walk(nested)
                elif isinstance(value, list):
                    for nested in value:
                        walk(nested)
            walk(payload)

        page.on("response", inspect_response)
        await page.goto(claim["start_url"], wait_until="domcontentloaded", timeout=90_000)
        response = HTMLResponse(_page(ACTIVE.session_id))
        response.set_cookie(
            TOKEN_COOKIE,
            secret,
            httponly=True,
            secure=_cookie_secure_for_public_url(),
            samesite="strict",
            max_age=SESSION_TIMEOUT,
        )
        return response


async def _list_choices(active: ActiveLogin) -> list[dict[str, str]]:
    choices = dict(active.discovered_lists)
    for page in active.context.pages:
        for anchor in await page.locator("a[href]").all():
            try:
                href = await anchor.get_attribute("href") or ""
                text = " ".join((await anchor.inner_text()).split())
            except Exception:
                continue
            match = re.search(r"/(?:list|lists|shopping-list)/([A-Za-z0-9_-]{6,})", urlparse(href).path)
            if match and text:
                choices[match.group(1)] = text[:200]
        current = urlparse(page.url)
        match = re.search(r"/(?:list|lists|shopping-list)/([A-Za-z0-9_-]{6,})", current.path)
        if match:
            title = " ".join((await page.title()).split()) or "Samsung Food-liste"
            choices.setdefault(match.group(1), title[:200])
    return [{"id": key, "name": value} for key, value in choices.items()]


@app.post("/session/{session_id}/complete")
async def complete_session(session_id: str, request: Request) -> dict:
    global ACTIVE
    async with LOCK:
        active = _current(request, session_id)
        cookies = await active.context.cookies()
        token = next((item.get("value") for item in cookies if item.get("name") == "whisk.USER_TOKEN"), None)
        if not token:
            raise HTTPException(status_code=409, detail="Samsung-login er ikke færdigt endnu")
        lists = await _list_choices(active)
        if not lists:
            raise HTTPException(status_code=409, detail="Åbn Lister i Samsung Food og vælg mindst én indkøbsliste")
        temporary = active.auth_state.with_suffix(".tmp")
        temporary.write_text(json.dumps({"token": token, "updated_at": time.time(), "source": "interactive-broker"}, indent=2), "utf-8")
        temporary.replace(active.auth_state)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{MOBILE_API}/api/mobile/v1/integrations/samsung-food/broker/session/{session_id}/complete",
                headers={"X-Kurv-Broker-Key": BROKER_KEY}, json={"lists": lists},
            )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="QNAP kunne ikke afslutte login-sessionen")
        await active.context.close()
        ACTIVE = None
        return {"ok": True, "list_count": len(lists)}


@app.websocket("/ws/{session_id}")
async def vnc_proxy(websocket: WebSocket, session_id: str) -> None:
    if not ACTIVE or ACTIVE.session_id != session_id or websocket.cookies.get(TOKEN_COOKIE) != ACTIVE.secret:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
    except OSError:
        await websocket.close(code=1011)
        return

    async def browser_to_user() -> None:
        while data := await reader.read(65536):
            await websocket.send_bytes(data)

    async def user_to_browser() -> None:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                writer.write(message["bytes"])
                await writer.drain()

    tasks = [asyncio.create_task(browser_to_user()), asyncio.create_task(user_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        writer.close()
        await writer.wait_closed()

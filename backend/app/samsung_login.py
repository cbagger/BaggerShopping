from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .households import HouseholdContext, require_household, require_owner, update_household

router = APIRouter(prefix="/api/mobile/v1/integrations/samsung-food", tags=["samsung-login"])
LOCK = asyncio.Lock()
SESSION_LIFETIME = 15 * 60


class SamsungListChoice(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class CompleteLoginRequest(BaseModel):
    lists: list[SamsungListChoice] = Field(min_length=1, max_length=100)


class SelectListRequest(BaseModel):
    session_id: str
    list_id: str


def _path() -> Path:
    return Path(os.getenv("SAMSUNG_LOGIN_SESSION_STORE_PATH", "/data/samsung-login-sessions.json"))


def _base_url() -> str | None:
    value = os.getenv("SAMSUNG_LOGIN_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return value or None


def _load() -> dict[str, Any]:
    try:
        value = json.loads(_path().read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(value: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    temporary.replace(path)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _family_paths(household_id: str) -> dict[str, str]:
    root = Path("/data/integrations") / household_id / "samsung-food"
    return {
        "root": str(root),
        "browser_profile": str(root / "chromium-profile"),
        "auth_state": str(root / "auth-state.json"),
    }


def _active_session(store: dict[str, Any], session_id: str) -> dict[str, Any]:
    session = store.get("sessions", {}).get(session_id)
    if not session or session.get("expires_at", 0) <= int(time.time()):
        raise HTTPException(status_code=404, detail="Login-sessionen findes ikke eller er udløbet")
    return session


@router.post("/login/start")
async def start_login(context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    require_owner(context)
    base_url = _base_url()
    if not base_url:
        raise HTTPException(status_code=503, detail="Det sikre Samsung-login er ikke aktiveret på QNAP endnu")
    raw_token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    now = int(time.time())
    async with LOCK:
        store = _load()
        sessions = store.setdefault("sessions", {})
        for key, value in list(sessions.items()):
            if value.get("expires_at", 0) <= now:
                sessions.pop(key, None)
        sessions[session_id] = {
            "id": session_id,
            "household_id": context.household_id,
            "token_hash": _hash(raw_token),
            "created_at": now,
            "expires_at": now + SESSION_LIFETIME,
            "status": "awaiting_login",
            "paths": _family_paths(context.household_id),
        }
        _save(store)
    return {
        "ok": True,
        "session_id": session_id,
        "login_url": f"{base_url}/session/{raw_token}",
        "expires_at": now + SESSION_LIFETIME,
    }


@router.get("/login/{session_id}")
async def login_status(session_id: str, context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    require_owner(context)
    async with LOCK:
        session = _active_session(_load(), session_id)
        if session["household_id"] != context.household_id:
            raise HTTPException(status_code=404, detail="Login-sessionen findes ikke")
        return {
            "ok": True,
            "session_id": session_id,
            "status": session["status"],
            "expires_at": session["expires_at"],
            "lists": session.get("lists", []),
        }


@router.get("/broker/session/{login_token}")
async def broker_session(login_token: str) -> dict[str, Any]:
    """One-time capability used only by the isolated QNAP login broker."""
    async with LOCK:
        store = _load()
        session = next(
            (value for value in store.get("sessions", {}).values() if secrets.compare_digest(value.get("token_hash", ""), _hash(login_token))),
            None,
        )
        if not session or session.get("expires_at", 0) <= int(time.time()):
            raise HTTPException(status_code=404, detail="Login-sessionen findes ikke eller er udløbet")
        if session.get("broker_claimed_at"):
            raise HTTPException(status_code=409, detail="Login-sessionen er allerede åbnet")
        session["broker_claimed_at"] = int(time.time())
        session["status"] = "login_open"
        # Rotate away the URL capability immediately. A copied browser URL can
        # therefore not reopen or inspect the family session.
        session["token_hash"] = _hash(secrets.token_urlsafe(32))
        _save(store)
        return {
            "session_id": session["id"],
            "household_id": session["household_id"],
            "expires_at": session["expires_at"],
            "browser_profile": session["paths"]["browser_profile"],
            "auth_state": session["paths"]["auth_state"],
            "start_url": "https://app.samsungfood.com",
        }


@router.post("/broker/session/{session_id}/complete")
async def broker_complete(
    session_id: str,
    request: CompleteLoginRequest,
    broker_key: str | None = Header(default=None, alias="X-Kurv-Broker-Key"),
) -> dict[str, Any]:
    """Called on the private container network after the user finishes login."""
    expected = os.getenv("SAMSUNG_LOGIN_BROKER_KEY", "")
    if not expected or not broker_key or not secrets.compare_digest(expected, broker_key):
        raise HTTPException(status_code=401, detail="Ugyldig login-broker")
    async with LOCK:
        store = _load()
        session = _active_session(store, session_id)
        if session.get("status") != "login_open":
            raise HTTPException(status_code=409, detail="Login-sessionen er ikke klar")
        session["lists"] = [value.model_dump() for value in request.lists]
        session["status"] = "choose_list"
        session["completed_at"] = int(time.time())
        _save(store)
    return {"ok": True}


@router.post("/login/select-list")
async def select_list(request: SelectListRequest, context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    require_owner(context)
    async with LOCK:
        store = _load()
        session = _active_session(store, request.session_id)
        if session["household_id"] != context.household_id or session.get("status") != "choose_list":
            raise HTTPException(status_code=409, detail="Login-sessionen er ikke klar til listevalg")
        selected = next((value for value in session.get("lists", []) if value["id"] == request.list_id), None)
        if not selected:
            raise HTTPException(status_code=422, detail="Den valgte Samsung-liste findes ikke")

        paths = session["paths"]
        session["status"] = "activated"
        session["activated_at"] = int(time.time())
        _save(store)

    def activate(household: dict[str, Any]) -> None:
        integration = household.setdefault("integrations", {}).setdefault("samsung_food", {})
        integration.update(
            status="connected",
            list_id=selected["id"],
            list_name=selected["name"],
            storage_scope=context.household_id,
            auth_state_path=paths["auth_state"],
            browser_profile_path=paths["browser_profile"],
            connected_at=int(time.time()),
            error_message=None,
        )
        household["list_backend"] = "samsung"

    await update_household(context, activate)
    return {"ok": True, "list_id": selected["id"], "list_name": selected["name"]}

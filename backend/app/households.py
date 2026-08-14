from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/mobile/v1/households", tags=["households"])
LOCK = asyncio.Lock()
LEGACY_HOUSEHOLD_ID = "family-bagger"


class HouseholdContext(BaseModel):
    household_id: str
    household_name: str
    member_name: str
    role: Literal["owner", "member"]
    list_backend: Literal["samsung", "local"]


CURRENT_HOUSEHOLD: ContextVar[HouseholdContext | None] = ContextVar("current_household", default=None)


class CreateHouseholdRequest(BaseModel):
    household_name: str = Field(min_length=1, max_length=80)
    member_name: str = Field(min_length=1, max_length=80)


class JoinHouseholdRequest(BaseModel):
    invite_code: str = Field(min_length=6, max_length=20)
    member_name: str = Field(min_length=1, max_length=80)


class RecoverHouseholdRequest(BaseModel):
    recovery_code: str = Field(min_length=12, max_length=64)
    member_name: str = Field(min_length=1, max_length=80)


class InviteRequest(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class UpdateMemberRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _member_id() -> str:
    return str(uuid.uuid4())


def store_path() -> Path:
    return Path(os.getenv("HOUSEHOLD_STORE_PATH", "/data/households.json"))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return "kurv_" + secrets.token_urlsafe(32)


def _new_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _new_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    chunks = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4)]
    return "-".join(chunks)


def _clean_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def load_store() -> dict[str, Any]:
    try:
        raw = json.loads(store_path().read_text("utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_store(store: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    temporary.replace(path)


def ensure_legacy_household(store: dict[str, Any]) -> dict[str, Any]:
    households = store.setdefault("households", {})
    household = households.setdefault(LEGACY_HOUSEHOLD_ID, {
        "id": LEGACY_HOUSEHOLD_ID,
        "name": os.getenv("DEFAULT_HOUSEHOLD_NAME", "Familien Bagger"),
        "list_backend": "samsung",
        "created_at": int(time.time()),
        "members": {},
        "items": [],
        "offer_metadata": {},
    })
    household.setdefault("owner", {"id": "legacy-owner", "name": "Christoffer", "role": "owner"})
    migrate_member_ids(household)
    return household


def migrate_member_ids(household: dict[str, Any]) -> bool:
    """Give pre-v2 members stable IDs without changing their token hash."""
    changed = False
    for member in household.setdefault("members", {}).values():
        if not isinstance(member.get("id"), str) or not member["id"]:
            member["id"] = _member_id()
            changed = True
    return changed


def context_from_record(household: dict[str, Any], member: dict[str, Any]) -> HouseholdContext:
    return HouseholdContext(
        household_id=household["id"], household_name=household["name"],
        member_name=member.get("name", "Familiemedlem"), role=member.get("role", "member"),
        list_backend=household.get("list_backend", "local"),
    )


def set_current(context: HouseholdContext) -> HouseholdContext:
    CURRENT_HOUSEHOLD.set(context)
    return context


def current_household() -> HouseholdContext:
    context = CURRENT_HOUSEHOLD.get()
    if context is None:
        raise HTTPException(status_code=401, detail="Familiekontekst mangler")
    return context


def legacy_worker_context() -> HouseholdContext:
    return set_current(HouseholdContext(
        household_id=LEGACY_HOUSEHOLD_ID,
        household_name=os.getenv("DEFAULT_HOUSEHOLD_NAME", "Familien Bagger"),
        member_name="QNAP worker",
        role="owner",
        list_backend="samsung",
    ))


async def require_household(
    authorization: str | None = Header(default=None),
) -> HouseholdContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    async with LOCK:
        store = load_store()
        legacy = ensure_legacy_household(store)
        expected = os.getenv("MOBILE_API_TOKEN", "")
        if expected and not legacy.get("legacy_token_disabled", False) and secrets.compare_digest(supplied, expected):
            return set_current(HouseholdContext(
                household_id=legacy["id"], household_name=legacy["name"],
                member_name="Christoffer", role="owner", list_backend="samsung",
            ))
        token_hash = _hash(supplied)
        for household in store.get("households", {}).values():
            member = household.get("members", {}).get(token_hash)
            if member:
                return set_current(context_from_record(household, member))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")


@router.get("/me")
async def household_me(context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    return {"ok": True, **context.model_dump()}


@router.post("/create")
async def create_household(request: CreateHouseholdRequest) -> dict[str, Any]:
    household_id = str(uuid.uuid4())
    token = _new_token()
    recovery_code = _new_recovery_code()
    household = {
        "id": household_id, "name": request.household_name.strip(), "list_backend": "local",
        "created_at": int(time.time()), "items": [], "offer_metadata": {},
        "members": {_hash(token): {"id": _member_id(), "name": request.member_name.strip(), "role": "owner", "created_at": int(time.time())}},
        "recovery_code_hash": _hash(_clean_code(recovery_code)),
    }
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        store.setdefault("households", {})[household_id] = household
        save_store(store)
    return {"ok": True, "access_token": token, "recovery_code": recovery_code, **context_from_record(household, next(iter(household["members"].values()))).model_dump()}


@router.get("/recovery")
async def recovery_status(context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    require_owner(context)
    async with LOCK:
        store = load_store()
        household = ensure_legacy_household(store) if context.household_id == LEGACY_HOUSEHOLD_ID else store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        configured = bool(household.get("recovery_code_hash"))
    return {"ok": True, "configured": configured}


@router.post("/recovery/rotate")
async def rotate_recovery_code(
    context: HouseholdContext = Depends(require_household),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_owner(context)
    code = _new_recovery_code()
    async with LOCK:
        store = load_store()
        household = ensure_legacy_household(store) if context.household_id == LEGACY_HOUSEHOLD_ID else store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        household["recovery_code_hash"] = _hash(_clean_code(code))

        # Convert Familien Baggers historic environment token into a normal,
        # revocable server member without changing family ID or list backend.
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        expected = os.getenv("MOBILE_API_TOKEN", "")
        if context.household_id == LEGACY_HOUSEHOLD_ID and expected and secrets.compare_digest(supplied, expected):
            household.setdefault("members", {}).setdefault(
                _hash(supplied),
                {"id": _member_id(), "name": household.get("owner", {}).get("name", "Christoffer"), "role": "owner", "created_at": int(time.time())},
            )
            household["legacy_token_disabled"] = True
        save_store(store)
    return {"ok": True, "recovery_code": code}


@router.post("/recover")
async def recover_household(request: RecoverHouseholdRequest) -> dict[str, Any]:
    code_hash = _hash(_clean_code(request.recovery_code))
    token = _new_token()
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        household = next(
            (value for value in store.get("households", {}).values() if value.get("recovery_code_hash") and secrets.compare_digest(value["recovery_code_hash"], code_hash)),
            None,
        )
        if not household:
            raise HTTPException(status_code=404, detail="Gendannelseskoden er ugyldig")
        member = {"id": _member_id(), "name": request.member_name.strip(), "role": "owner", "created_at": int(time.time())}
        household.setdefault("members", {})[_hash(token)] = member
        save_store(store)
    return {"ok": True, "access_token": token, **context_from_record(household, member).model_dump()}


@router.post("/invite")
async def create_invite(
    request: InviteRequest,
    context: HouseholdContext = Depends(require_household),
) -> dict[str, Any]:
    code = _new_code()
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        store.setdefault("invites", {})[_hash(code)] = {
            "household_id": context.household_id,
            "expires_at": int(time.time()) + request.expires_in_days * 86400,
        }
        save_store(store)
    return {"ok": True, "invite_code": code, "expires_in_days": request.expires_in_days}


@router.post("/join")
async def join_household(request: JoinHouseholdRequest) -> dict[str, Any]:
    code = "".join(request.invite_code.upper().split())
    token = _new_token()
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        invite = store.setdefault("invites", {}).pop(_hash(code), None)
        if not invite or invite.get("expires_at", 0) < int(time.time()):
            raise HTTPException(status_code=404, detail="Invitationskoden er ugyldig eller udløbet")
        household = store["households"].get(invite["household_id"])
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        member = {"id": _member_id(), "name": request.member_name.strip(), "role": "member", "created_at": int(time.time())}
        household.setdefault("members", {})[_hash(token)] = member
        save_store(store)
    return {"ok": True, "access_token": token, **context_from_record(household, member).model_dump()}


def require_owner(context: HouseholdContext) -> None:
    if context.role != "owner":
        raise HTTPException(status_code=403, detail="Kun familiens administrator kan administrere medlemmer")


@router.get("/members")
async def list_members(context: HouseholdContext = Depends(require_household)) -> dict[str, Any]:
    require_owner(context)
    async with LOCK:
        store = load_store()
        household = ensure_legacy_household(store) if context.household_id == LEGACY_HOUSEHOLD_ID else store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        if migrate_member_ids(household):
            save_store(store)
        members = []
        if context.household_id == LEGACY_HOUSEHOLD_ID and not any(member.get("role") == "owner" for member in household.get("members", {}).values()):
            members.append(household["owner"])
        members.extend({"id": member.get("id"), "name": member.get("name"), "role": member.get("role", "member")} for member in household.get("members", {}).values())
    return {"ok": True, "members": members}


@router.patch("/members/{member_id}")
async def update_member(
    member_id: str,
    request: UpdateMemberRequest,
    context: HouseholdContext = Depends(require_household),
) -> dict[str, Any]:
    require_owner(context)
    name = request.name.strip()
    async with LOCK:
        store = load_store()
        household = ensure_legacy_household(store) if context.household_id == LEGACY_HOUSEHOLD_ID else store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        if member_id == "legacy-owner":
            household["owner"]["name"] = name
        else:
            member = next((value for value in household.get("members", {}).values() if value.get("id") == member_id), None)
            if not member:
                raise HTTPException(status_code=404, detail="Familiemedlemmet findes ikke")
            member["name"] = name
        save_store(store)
    return {"ok": True, "member_id": member_id, "name": name}


@router.delete("/members/{member_id}")
async def remove_member(
    member_id: str,
    context: HouseholdContext = Depends(require_household),
) -> dict[str, Any]:
    require_owner(context)
    if member_id == "legacy-owner":
        raise HTTPException(status_code=409, detail="Familiens administrator kan ikke slettes")
    async with LOCK:
        store = load_store()
        household = ensure_legacy_household(store) if context.household_id == LEGACY_HOUSEHOLD_ID else store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        token_hash = next((key for key, value in household.get("members", {}).items() if value.get("id") == member_id), None)
        if token_hash is None:
            raise HTTPException(status_code=404, detail="Familiemedlemmet findes ikke")
        member = household["members"][token_hash]
        if member.get("role") == "owner":
            owners = [value for value in household.get("members", {}).values() if value.get("role") == "owner"]
            if len(owners) <= 1:
                raise HTTPException(status_code=409, detail="Familiens sidste administrator kan ikke slettes")
        del household["members"][token_hash]
        save_store(store)
    return {"ok": True, "removed": True}


async def read_household(context: HouseholdContext) -> dict[str, Any]:
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        household = store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        return json.loads(json.dumps(household))


async def update_household(context: HouseholdContext, mutation) -> Any:
    async with LOCK:
        store = load_store()
        ensure_legacy_household(store)
        household = store.get("households", {}).get(context.household_id)
        if not household:
            raise HTTPException(status_code=404, detail="Familien findes ikke")
        result = mutation(household)
        save_store(store)
        return result

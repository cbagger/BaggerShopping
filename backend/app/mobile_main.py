from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .mobile_offer_metadata import router as offer_metadata_router
from .mobile_offers import router as offers_router
from .product_identity import router as product_identity_router
from .samsung_login import router as samsung_login_router
from .households import HouseholdContext, read_household, require_household, require_owner, router as households_router, update_household
from .flyer_push import router as flyer_push_router


class MobileSettings(BaseSettings):
    mobile_api_token: str
    core_api_base: str = "http://bagger-shopping:8080"
    request_timeout_seconds: float = 20.0
    category_store_path: str = "/data/category-overrides.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = MobileSettings()
category_store_lock = asyncio.Lock()

app = FastAPI(
    title="Bagger Shopping Mobile API",
    version="0.20.0",
    description="Internet-facing authenticated API for the Bagger Shopping iPhone app.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class MobileItem(BaseModel):
    id: str | None = None
    name: str
    checked: bool = False
    quantity: float | None = None
    unit: str | None = None


class MobileListResponse(BaseModel):
    ok: bool = True
    name: str
    count: int
    has_items: bool
    items: list[MobileItem]


class AddItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddItemResponse(BaseModel):
    ok: bool
    name: str


class SetCheckedRequest(BaseModel):
    checked: bool


class SetQuantityRequest(BaseModel):
    quantity: float = Field(gt=0, le=999)
    unit: str = Field(default="stk", min_length=1, max_length=20)


class CategoryOverride(BaseModel):
    item_name: str
    category: str


class CategoryOverridesResponse(BaseModel):
    ok: bool = True
    overrides: list[CategoryOverride]


class CategoryOverrideRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)


class CategoryOverrideRemoveRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)


class SamsungIntegrationResponse(BaseModel):
    provider: str = "samsung_food"
    status: str
    list_name: str | None = None
    list_id: str | None = None
    last_successful_sync: int | None = None
    error_message: str | None = None
    can_manage: bool
    self_service_login_available: bool = False


def _integration_record(household: dict[str, Any]) -> dict[str, Any]:
    integrations = household.setdefault("integrations", {})
    samsung = integrations.setdefault("samsung_food", {})
    if household.get("list_backend") == "samsung":
        # Legacy migration is metadata-only on purpose: the active QNAP auth
        # state and Samsung list are retained until the isolated login flow can
        # replace them atomically.
        samsung.setdefault("status", "connected")
        samsung.setdefault("storage_scope", household["id"])
        samsung.setdefault("migrated_from_legacy", household["id"] == "family-bagger")
    else:
        samsung.setdefault("status", "not_connected")
    return samsung


async def _set_integration_health(
    context: HouseholdContext,
    *,
    status_value: str,
    list_name: str | None = None,
    list_id: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    def mutate(household: dict[str, Any]) -> dict[str, Any]:
        record = _integration_record(household)
        record["status"] = status_value
        record["error_message"] = error_message
        if list_name:
            record["list_name"] = list_name
        if list_id:
            record["list_id"] = list_id
        if status_value == "connected":
            record["last_successful_sync"] = int(time.time())
        return dict(record)
    return await update_household(context, mutate)


async def require_mobile_token(
    authorization: str | None = Header(default=None),
) -> HouseholdContext:
    return await require_household(authorization)


async def household_context(authorization: str | None = Header(default=None)) -> HouseholdContext:
    return await require_mobile_token(authorization)


def category_key(item_name: str) -> str:
    return " ".join(item_name.casefold().strip().split())


def load_category_store() -> dict[str, dict[str, str]]:
    path = Path(settings.category_store_path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        item_name = value.get("item_name")
        category = value.get("category")
        if isinstance(item_name, str) and isinstance(category, str):
            result[key] = {"item_name": item_name, "category": category}
    return result


def save_category_store(store: dict[str, dict[str, str]]) -> None:
    path = Path(settings.category_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


async def core_get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client:
        return await client.get(f"{settings.core_api_base}{path}")


async def core_post(path: str, json: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client:
        return await client.post(f"{settings.core_api_base}{path}", json=json)


async def core_patch(path: str, json: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client:
        return await client.patch(f"{settings.core_api_base}{path}", json=json)


async def core_delete(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client:
        return await client.delete(f"{settings.core_api_base}{path}")


async def family_samsung_client(context: HouseholdContext) -> Any | None:
    household = await read_household(context)
    integration = household.get("integrations", {}).get("samsung_food", {})
    list_id = integration.get("list_id")
    auth_state = integration.get("auth_state_path")
    browser_profile = integration.get("browser_profile_path")
    if not all(isinstance(value, str) and value for value in (list_id, auth_state, browser_profile)):
        return None
    # Keep the public/local-family mobile API importable without requiring the
    # legacy SAMSUNG_LIST_ID environment variable.
    from .auth import SamsungAuthManager
    from .samsung import SamsungFoodClient
    auth = SamsungAuthManager(
        state_file=Path(auth_state),
        browser_user_data_dir=Path(browser_profile),
        allow_credential_fallback=False,
    )
    return SamsungFoodClient(list_id=list_id, auth=auth)


@app.get("/api/mobile/v1/health")
async def mobile_health(context: HouseholdContext = Depends(household_context)) -> dict[str, Any]:
    if context.list_backend == "local":
        return {"ok": True, "service": "bagger-shopping-mobile", "core_status": "ok", "samsung_auth": "not_used", "requires_interaction": False}
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            await family_client.get_list()
            return {"ok": True, "service": "bagger-shopping-mobile", "core_status": "ok", "samsung_auth": "ok", "requires_interaction": False}
        except Exception:
            return {"ok": True, "service": "bagger-shopping-mobile", "core_status": "ok", "samsung_auth": "refresh-needed", "requires_interaction": True}
    try:
        response = await core_get("/api/health")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Core service unhealthy")
    core = response.json()
    return {
        "ok": True,
        "service": "bagger-shopping-mobile",
        "core_status": core.get("status"),
        "samsung_auth": core.get("samsung_auth"),
        "requires_interaction": core.get("requires_interaction", False),
    }


@app.get("/api/mobile/v1/list", response_model=MobileListResponse)
async def get_mobile_list(context: HouseholdContext = Depends(household_context)) -> MobileListResponse:
    if context.list_backend == "local":
        household = await read_household(context)
        items = [MobileItem.model_validate(item) for item in household.get("items", [])]
        return MobileListResponse(name=household["name"], count=len(items), has_items=bool(items), items=items)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            payload = await family_client.get_list()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        items = [MobileItem.model_validate(item.model_dump()) for item in payload.items]
        return MobileListResponse(name=payload.name or "Indkøbsliste", count=len(items), has_items=bool(items), items=items)
    try:
        response = await core_get("/api/shopping")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core shopping request failed: {response.text[:500]}")

    payload = response.json()
    raw_items = payload.get("items") or []
    items = [
        MobileItem(
            id=item.get("id"),
            name=str(item.get("name")),
            checked=bool(item.get("checked", False)),
            quantity=float(item["quantity"]) if isinstance(item.get("quantity"), (int, float)) and not isinstance(item.get("quantity"), bool) else None,
            unit=item.get("unit") if isinstance(item.get("unit"), str) else None,
        )
        for item in raw_items
        if isinstance(item, dict) and item.get("name")
    ]
    return MobileListResponse(
        name=payload.get("name") or "Indkøbsliste",
        count=len(items),
        has_items=bool(items),
        items=items,
    )


@app.get("/api/mobile/v1/category-overrides", response_model=CategoryOverridesResponse)
async def get_category_overrides(_: None = Depends(require_mobile_token)) -> CategoryOverridesResponse:
    async with category_store_lock:
        store = load_category_store()
    return CategoryOverridesResponse(
        overrides=[CategoryOverride(item_name=v["item_name"], category=v["category"]) for v in store.values()]
    )


@app.put("/api/mobile/v1/category-overrides")
async def put_category_override(
    request: CategoryOverrideRequest,
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
    item_name = request.item_name.strip()
    category = request.category.strip()
    key = category_key(item_name)
    if not key:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")
    async with category_store_lock:
        store = load_category_store()
        store[key] = {"item_name": item_name, "category": category}
        save_category_store(store)
    return {"ok": True, "item_name": item_name, "category": category}


@app.post("/api/mobile/v1/category-overrides/remove")
async def remove_category_override(
    request: CategoryOverrideRemoveRequest,
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
    key = category_key(request.item_name)
    async with category_store_lock:
        store = load_category_store()
        removed = store.pop(key, None) is not None
        save_category_store(store)
    return {"ok": True, "removed": removed}


@app.delete("/api/mobile/v1/category-overrides")
async def clear_category_overrides(_: None = Depends(require_mobile_token)) -> dict[str, Any]:
    async with category_store_lock:
        save_category_store({})
    return {"ok": True}


@app.post("/api/mobile/v1/items", response_model=AddItemResponse)
async def add_mobile_item(
    request: AddItemRequest,
    context: HouseholdContext = Depends(household_context),
) -> AddItemResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")
    if context.list_backend == "local":
        await update_household(context, lambda household: household.setdefault("items", []).append({
            "id": str(uuid.uuid4()), "name": name, "checked": False, "quantity": None, "unit": None,
            "created_at": int(time.time()),
        }))
        return AddItemResponse(ok=True, name=name)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            await family_client.add_item(name)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return AddItemResponse(ok=True, name=name)
    try:
        response = await core_post("/api/shopping/items", {"name": name})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core add-item request failed: {response.text[:500]}")
    return AddItemResponse(ok=True, name=name)


@app.patch("/api/mobile/v1/items/{item_id}/checked")
async def set_mobile_item_checked(
    item_id: str,
    request: SetCheckedRequest,
    context: HouseholdContext = Depends(household_context),
) -> dict[str, Any]:
    if context.list_backend == "local":
        def mutate(household):
            for item in household.setdefault("items", []):
                if item.get("id") == item_id:
                    item["checked"] = request.checked
                    return {"ok": True, "item_id": item_id}
            raise HTTPException(status_code=404, detail="Varen findes ikke i familien")
        return await update_household(context, mutate)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            return {"ok": True, **await family_client.set_item_checked(item_id, request.checked)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        response = await core_patch(f"/api/shopping/items/{item_id}/checked", {"checked": request.checked})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core check-item request failed: {response.text[:500]}")
    return response.json()


@app.patch("/api/mobile/v1/items/{item_id}/quantity")
async def set_mobile_item_quantity(
    item_id: str,
    request: SetQuantityRequest,
    context: HouseholdContext = Depends(household_context),
) -> dict[str, Any]:
    if context.list_backend == "local":
        def mutate(household):
            for item in household.setdefault("items", []):
                if item.get("id") == item_id:
                    item.update(quantity=request.quantity, unit=request.unit)
                    return {"ok": True, "item_id": item_id}
            raise HTTPException(status_code=404, detail="Varen findes ikke i familien")
        return await update_household(context, mutate)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            return {"ok": True, **await family_client.set_item_quantity(item_id, request.quantity, request.unit)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        response = await core_patch(
            f"/api/shopping/items/{item_id}/quantity",
            {"quantity": request.quantity, "unit": request.unit},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core quantity request failed: {response.text[:500]}")
    return response.json()


@app.delete("/api/mobile/v1/items/{item_id}")
async def delete_mobile_item(
    item_id: str,
    context: HouseholdContext = Depends(household_context),
) -> dict[str, Any]:
    if context.list_backend == "local":
        def mutate(household):
            items = household.setdefault("items", [])
            before = len(items)
            household["items"] = [item for item in items if item.get("id") != item_id]
            if len(household["items"]) == before:
                raise HTTPException(status_code=404, detail="Varen findes ikke i familien")
            return {"ok": True, "item_id": item_id}
        return await update_household(context, mutate)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            return {"ok": True, **await family_client.delete_item(item_id)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        response = await core_delete(f"/api/shopping/items/{item_id}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core delete-item request failed: {response.text[:500]}")
    return response.json()


@app.delete("/api/mobile/v1/actions/clear-checked")
async def delete_all_checked_mobile_items(
    context: HouseholdContext = Depends(household_context),
) -> dict[str, Any]:
    if context.list_backend == "local":
        def mutate(household):
            items = household.setdefault("items", [])
            deleted = [item.get("id") for item in items if item.get("checked")]
            household["items"] = [item for item in items if not item.get("checked")]
            return {"ok": True, "deleted_count": len(deleted), "deleted_item_ids": deleted}
        return await update_household(context, mutate)
    family_client = await family_samsung_client(context)
    if family_client is not None:
        try:
            current = await family_client.get_list()
            deleted: list[str] = []
            for item in current.items:
                if item.checked is True and item.id:
                    await family_client.delete_item(item.id)
                    deleted.append(item.id)
            return {"ok": True, "deleted_count": len(deleted), "deleted_item_ids": deleted}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        response = await core_delete("/api/shopping/actions/clear-checked")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core clear-checked request failed: {response.text[:500]}")
    return response.json()


@app.get("/api/mobile/v1/integrations/samsung-food", response_model=SamsungIntegrationResponse)
async def samsung_integration_status(
    context: HouseholdContext = Depends(household_context),
) -> SamsungIntegrationResponse:
    household = await read_household(context)
    record = _integration_record(household)

    if context.list_backend == "samsung":
        family_client = await family_samsung_client(context)
        try:
            if family_client is not None:
                family_list = await family_client.get_list()
                payload = family_list.model_dump()
                record = await _set_integration_health(
                    context,
                    status_value="connected",
                    list_name=family_list.name or record.get("list_name") or "Indkøbsliste",
                    list_id=family_list.list_id or record.get("list_id"),
                )
            else:
                health = await core_get("/api/health")
                shopping = await core_get("/api/shopping")
                if health.status_code != 200 or shopping.status_code != 200:
                    raise RuntimeError("Legacy-sessionen er udløbet")
                payload = shopping.json()
                record = await _set_integration_health(
                    context, status_value="connected",
                    list_name=payload.get("name") or record.get("list_name") or "Indkøbsliste",
                    list_id=payload.get("list_id") or record.get("list_id"),
                )
        except Exception:
            record = await _set_integration_health(
                context,
                status_value="requires_reconnect",
                error_message="Kurv kunne ikke kontakte Samsung Food. Familiens varer er ikke slettet.",
            )
    else:
        # Persist the family-owned integration envelope even for families that
        # choose to use Kurv without Samsung Food.
        record = await update_household(context, lambda value: dict(_integration_record(value)))

    return SamsungIntegrationResponse(
        status=record.get("status", "not_connected"),
        list_name=record.get("list_name"),
        list_id=record.get("list_id"),
        last_successful_sync=record.get("last_successful_sync"),
        error_message=record.get("error_message"),
        can_manage=context.role == "owner",
        self_service_login_available=bool(os.getenv("SAMSUNG_LOGIN_PUBLIC_BASE_URL", "").strip()),
    )


@app.post("/api/mobile/v1/integrations/samsung-food/disconnect")
async def disconnect_samsung_integration(
    context: HouseholdContext = Depends(household_context),
) -> dict[str, Any]:
    require_owner(context)
    preserved_items: list[dict[str, Any]] = []
    preserved_name = context.household_name
    if context.list_backend == "samsung":
        family_client = await family_samsung_client(context)
        try:
            if family_client is not None:
                family_list = await family_client.get_list()
                payload = family_list.model_dump()
            else:
                response = await core_get("/api/shopping")
                if response.status_code != 200:
                    raise RuntimeError("Legacy-listen kunne ikke læses")
                payload = response.json()
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Samsung-listen kunne ikke kopieres sikkert før afbrydelse") from exc
        preserved_name = payload.get("name") or preserved_name
        for item in payload.get("items") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            preserved_items.append({
                "id": str(uuid.uuid4()),
                "name": str(item["name"]),
                "checked": bool(item.get("checked", False)),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "created_at": int(time.time()),
            })

    def disconnect(household: dict[str, Any]) -> None:
        if household.get("list_backend") == "samsung":
            household["items"] = preserved_items
            household["list_backend"] = "local"
        record = _integration_record(household)
        record.update(
            status="not_connected",
            error_message=None,
            disconnected_at=int(time.time()),
            preserved_list_name=preserved_name,
        )

    await update_household(context, disconnect)
    return {"ok": True, "preserved_list_name": preserved_name, "preserved_item_count": len(preserved_items)}


app.include_router(offer_metadata_router, dependencies=[Depends(household_context)])
app.include_router(households_router)
app.include_router(offers_router, dependencies=[Depends(household_context)])
app.include_router(product_identity_router, dependencies=[Depends(household_context)])
app.include_router(samsung_login_router)
app.include_router(flyer_push_router, dependencies=[Depends(household_context)])

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .mobile_offers import router as offers_router


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
    version="0.12.0",
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


def require_mobile_token(
    authorization: str | None = Header(default=None),
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied = authorization.removeprefix("Bearer ").strip()
    expected = settings.mobile_api_token

    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


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


@app.get("/api/mobile/v1/health")
async def mobile_health(_: None = Depends(require_mobile_token)) -> dict[str, Any]:
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
async def get_mobile_list(_: None = Depends(require_mobile_token)) -> MobileListResponse:
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
    _: None = Depends(require_mobile_token),
) -> AddItemResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")
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
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
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
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
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
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
    try:
        response = await core_delete(f"/api/shopping/items/{item_id}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core delete-item request failed: {response.text[:500]}")
    return response.json()


app.include_router(offers_router, dependencies=[Depends(require_mobile_token)])

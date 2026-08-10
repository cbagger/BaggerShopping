from __future__ import annotations

import secrets
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MobileSettings(BaseSettings):
    mobile_api_token: str
    core_api_base: str = "http://bagger-shopping:8080"
    request_timeout_seconds: float = 20.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = MobileSettings()

app = FastAPI(
    title="Bagger Shopping Mobile API",
    version="0.5.0",
    description="Internet-facing authenticated API for the Bagger Shopping iPhone app.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class MobileItem(BaseModel):
    id: str | None = None
    name: str
    checked: bool = False


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


async def core_get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
    ) as client:
        return await client.get(f"{settings.core_api_base}{path}")


async def core_post(path: str, json: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
    ) as client:
        return await client.post(
            f"{settings.core_api_base}{path}",
            json=json,
        )


async def core_patch(path: str, json: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
    ) as client:
        return await client.patch(
            f"{settings.core_api_base}{path}",
            json=json,
        )


async def core_delete(path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
    ) as client:
        return await client.delete(f"{settings.core_api_base}{path}")


@app.get("/api/mobile/v1/health")
async def mobile_health(
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
    try:
        response = await core_get("/api/health")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Core service unavailable: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Core service unhealthy",
        )

    core = response.json()
    return {
        "ok": True,
        "service": "bagger-shopping-mobile",
        "core_status": core.get("status"),
        "samsung_auth": core.get("samsung_auth"),
        "requires_interaction": core.get("requires_interaction", False),
    }


@app.get("/api/mobile/v1/list", response_model=MobileListResponse)
async def get_mobile_list(
    _: None = Depends(require_mobile_token),
) -> MobileListResponse:
    try:
        response = await core_get("/api/shopping")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Core service unavailable: {exc}",
        ) from exc

    if response.status_code != 200:
        detail = response.text[:500]
        raise HTTPException(
            status_code=502,
            detail=f"Core shopping request failed: {detail}",
        )

    payload = response.json()
    raw_items = payload.get("items") or []

    items = [
        MobileItem(
            id=item.get("id"),
            name=str(item.get("name")),
            checked=bool(item.get("checked", False)),
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


@app.post("/api/mobile/v1/items", response_model=AddItemResponse)
async def add_mobile_item(
    request: AddItemRequest,
    _: None = Depends(require_mobile_token),
) -> AddItemResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")

    try:
        response = await core_post(
            "/api/shopping/items",
            {"name": name},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Core service unavailable: {exc}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Core add-item request failed: {response.text[:500]}",
        )

    return AddItemResponse(ok=True, name=name)


class SetCheckedRequest(BaseModel):
    checked: bool


@app.patch("/api/mobile/v1/items/{item_id}/checked")
async def set_mobile_item_checked(
    item_id: str,
    request: SetCheckedRequest,
    _: None = Depends(require_mobile_token),
) -> dict[str, Any]:
    try:
        response = await core_patch(
            f"/api/shopping/items/{item_id}/checked",
            {"checked": request.checked},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Core service unavailable: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Core check-item request failed: {response.text[:500]}")
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

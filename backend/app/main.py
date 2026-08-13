from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI, HTTPException

from .auth import SamsungAuthManager, AuthInteractionRequired
from .models import (
    AddItemRequest,
    AddItemResponse,
    AuthStatusResponse,
    HealthResponse,
    HomeAssistantShoppingResponse,
    ItemMutationResponse,
    SetCheckedRequest,
    SetQuantityRequest,
    ShoppingListResponse,
)
from .samsung import SamsungFoodClient, SamsungFoodError


_auth_refresh_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        auth = SamsungAuthManager()
        state = auth.load_state()
        if state.token:
            await auth.token_valid(state.token)
    except Exception:
        pass
    yield


app = FastAPI(
    title="Bagger Shopping",
    version="0.6.0",
    description="Private Samsung Food / Family Hub shopping-list connector",
    lifespan=lifespan,
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    auth = SamsungAuthManager()
    state = auth.load_state()
    try:
        valid = await auth.token_valid(state.token)
    except Exception:
        valid = False
    return HealthResponse(
        status="ok",
        samsung_auth="ok" if valid else "refresh-needed",
        requires_interaction=False,
    )


@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def auth_status() -> AuthStatusResponse:
    auth = SamsungAuthManager()
    state = auth.load_state()
    try:
        valid = await auth.token_valid(state.token)
    except Exception as exc:
        return AuthStatusResponse(
            ok=False,
            mode=state.source or "none",
            has_token=bool(state.token),
            token_valid=False,
            detail=f"Token validation failed: {exc}",
        )
    return AuthStatusResponse(
        ok=valid,
        mode=state.source or "none",
        has_token=bool(state.token),
        token_valid=valid,
    )


@app.post("/api/auth/refresh", response_model=AuthStatusResponse)
async def auth_refresh() -> AuthStatusResponse:
    auth = SamsungAuthManager()
    async with _auth_refresh_lock:
        try:
            token = await auth.get_token(force_refresh=True)
            return AuthStatusResponse(
                ok=True,
                mode=auth.load_state().source or "unknown",
                has_token=bool(token),
                token_valid=await auth.token_valid(token),
            )
        except AuthInteractionRequired as exc:
            return AuthStatusResponse(
                ok=False,
                mode="interaction-required",
                has_token=False,
                token_valid=False,
                requires_interaction=True,
                detail=str(exc),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/shopping", response_model=ShoppingListResponse)
async def get_shopping() -> ShoppingListResponse:
    try:
        return await SamsungFoodClient().get_list()
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/home-assistant/shopping", response_model=HomeAssistantShoppingResponse)
async def home_assistant_shopping() -> HomeAssistantShoppingResponse:
    try:
        current = await SamsungFoodClient().get_list()
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    names = [item.name for item in current.items if item.checked is not True]
    return HomeAssistantShoppingResponse(
        list_id=current.list_id,
        name=current.name,
        count=len(names),
        has_items=bool(names),
        items=names,
    )


@app.post("/api/shopping/items", response_model=AddItemResponse)
async def add_shopping_item(request: AddItemRequest) -> AddItemResponse:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")

    client = SamsungFoodClient()
    try:
        result = await client.add_item(name)
        # Samsung's SyncItems endpoint is eventually consistent. Returning after
        # a successful gRPC mutation keeps the mobile UI responsive; later reads
        # reconcile with Samsung instead of blocking this request on read-back.
        return AddItemResponse(
            ok=True,
            list_id=client.list_id,
            name=name,
            samsung_item_id=result.get("samsung_item_id"),
            grpc_status=result.get("grpc_status"),
        )
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.patch("/api/shopping/items/{item_id}/checked", response_model=ItemMutationResponse)
async def set_shopping_item_checked(
    item_id: str,
    request: SetCheckedRequest,
) -> ItemMutationResponse:
    try:
        result = await SamsungFoodClient().set_item_checked(item_id, request.checked)
        return ItemMutationResponse(
            ok=True,
            item_id=item_id,
            checked=request.checked,
            grpc_status=result.get("grpc_status"),
        )
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.patch("/api/shopping/items/{item_id}/quantity", response_model=ItemMutationResponse)
async def set_shopping_item_quantity(
    item_id: str,
    request: SetQuantityRequest,
) -> ItemMutationResponse:
    unit = request.unit.strip() or "stk"
    try:
        result = await SamsungFoodClient().set_item_quantity(
            item_id,
            request.quantity,
            unit,
        )
        return ItemMutationResponse(
            ok=True,
            item_id=item_id,
            quantity=request.quantity,
            unit=unit,
            grpc_status=result.get("grpc_status"),
        )
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/shopping/items/{item_id}", response_model=ItemMutationResponse)
async def delete_shopping_item(item_id: str) -> ItemMutationResponse:
    try:
        result = await SamsungFoodClient().delete_item(item_id)
        return ItemMutationResponse(
            ok=True,
            item_id=item_id,
            grpc_status=result.get("grpc_status"),
        )
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/shopping/actions/clear-checked")
async def delete_all_checked_shopping_items() -> dict[str, object]:
    client = SamsungFoodClient()
    try:
        current = await client.get_list()
        checked = [item for item in current.items if item.checked is True and item.id]
        deleted: list[str] = []
        failures: list[dict[str, str]] = []
        for item in checked:
            try:
                await client.delete_item(item.id)
                deleted.append(item.id)
            except SamsungFoodError as exc:
                failures.append({"item_id": item.id, "detail": str(exc)})
        if failures:
            raise HTTPException(
                status_code=502,
                detail={"message": "Nogle købte varer kunne ikke slettes", "deleted": deleted, "failures": failures},
            )
        return {"ok": True, "deleted_count": len(deleted), "deleted_item_ids": deleted}
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

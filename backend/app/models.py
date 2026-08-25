from typing import Any
from pydantic import BaseModel, Field


class ShoppingItem(BaseModel):
    id: str | None = None
    name: str
    checked: bool | None = None
    quantity: float | None = None
    unit: str | None = None
    raw: dict[str, Any] | None = None


class ShoppingListResponse(BaseModel):
    list_id: str
    name: str | None = None
    items: list[ShoppingItem] = Field(default_factory=list)


class AddItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddItemResponse(BaseModel):
    ok: bool
    list_id: str
    name: str
    samsung_item_id: str | None = None
    grpc_status: int | None = None


class AuthStatusResponse(BaseModel):
    ok: bool
    mode: str
    has_token: bool
    token_valid: bool
    requires_interaction: bool = False
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    samsung_auth: str
    requires_interaction: bool = False


class HomeAssistantShoppingResponse(BaseModel):
    ok: bool = True
    list_id: str
    name: str | None = None
    count: int
    has_items: bool
    items: list[str] = Field(default_factory=list)


class SetCheckedRequest(BaseModel):
    checked: bool


class SetQuantityRequest(BaseModel):
    quantity: float = Field(gt=0, le=999)
    unit: str = Field(default="stk", min_length=1, max_length=20)


class ItemMutationResponse(BaseModel):
    ok: bool
    item_id: str
    checked: bool | None = None
    quantity: float | None = None
    unit: str | None = None
    grpc_status: int | None = None
    item_name: str | None = None

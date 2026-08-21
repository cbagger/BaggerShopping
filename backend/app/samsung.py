from __future__ import annotations

import re
import time
from typing import Any

import httpx

from .auth import SamsungAuthManager
from .config import settings
from .grpc_web import (
    build_sync_items_add_request,
    build_sync_items_checked_request,
    build_sync_items_delete_request,
    build_sync_items_quantity_request,
    extract_printable_strings,
    parse_grpc_web_response,
)
from .models import ShoppingItem, ShoppingListResponse


class SamsungFoodError(RuntimeError):
    pass


class SamsungFoodClient:
    def __init__(self, *, list_id: str | None = None, auth: SamsungAuthManager | None = None) -> None:
        resolved_list_id = list_id or settings.samsung_list_id
        if not resolved_list_id:
            raise SamsungFoodError("Samsung Food list id is required when creating a client")
        self.list_id = resolved_list_id
        self.auth = auth or SamsungAuthManager()

    async def _token(self) -> str:
        try:
            return await self.auth.get_token()
        except Exception as exc:
            raise SamsungFoodError(str(exc)) from exc

    async def get_list(self) -> ShoppingListResponse:
        token = await self._token()
        url = f"{settings.samsung_food_api_base}/list/v2/{self.list_id}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "bagger-shopping/0.3",
        }

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 401:
            token = await self.auth.get_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=headers)

        if response.status_code >= 400:
            raise SamsungFoodError(
                f"Samsung Food READ failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        return self._normalize_list(response.json())

    def _normalize_list(self, payload: dict[str, Any]) -> ShoppingListResponse:
        list_meta = payload.get("list") or {}
        content = payload.get("content") or {}
        raw_items = content.get("items") or []

        items: list[ShoppingItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue

            nested_item = raw.get("item")
            name = None
            quantity = None
            unit = None
            if isinstance(nested_item, dict):
                name = nested_item.get("name")
                raw_quantity = nested_item.get("quantity")
                if isinstance(raw_quantity, (int, float)) and not isinstance(raw_quantity, bool):
                    quantity = float(raw_quantity)
                raw_unit = nested_item.get("unit")
                if isinstance(raw_unit, str) and raw_unit.strip():
                    unit = raw_unit.strip()

            name = name or raw.get("name")
            if not name:
                continue

            checked = raw.get("checked")
            if checked is None:
                checked = raw.get("is_checked")
            if checked is None:
                checked = raw.get("completed")

            normalized_name, typed_quantity = self._quantity_from_name(str(name))
            if quantity is None and typed_quantity is not None:
                quantity = typed_quantity
                unit = unit or "stk"

            items.append(
                ShoppingItem(
                    id=raw.get("id"),
                    name=normalized_name,
                    checked=checked if isinstance(checked, bool) else None,
                    quantity=quantity,
                    unit=unit,
                    raw=raw,
                )
            )

        list_name = (
            list_meta.get("name")
            or list_meta.get("title")
            or payload.get("name")
        )

        return ShoppingListResponse(
            list_id=self.list_id,
            name=list_name,
            items=items,
        )

    @staticmethod
    def _quantity_from_name(name: str) -> tuple[str, float | None]:
        """Extract an explicit final x/stk quantity without product rules."""
        value = " ".join(name.split())
        patterns = (
            r"^(?P<count>\d+(?:[.,]\d+)?)\s*[x×]\s*(?P<name>.+)$",
            r"^(?P<count>\d+(?:[.,]\d+)?)\s*(?:stk\.?|styk(?:ker)?)\s+(?P<name>.+)$",
            r"^(?P<name>.+?)\s+[x×]\s*(?P<count>\d+(?:[.,]\d+)?)$",
            r"^(?P<name>.+?)\s+(?P<count>\d+(?:[.,]\d+)?)\s*[x×]$",
            r"^(?P<name>.+?)\s+(?P<count>\d+(?:[.,]\d+)?)\s*(?:stk\.?|styk(?:ker)?)$",
        )
        for pattern in patterns:
            match = re.match(pattern, value, flags=re.IGNORECASE)
            if not match:
                continue
            count = float(match.group("count").replace(",", "."))
            product = match.group("name").strip(" -–—,")
            if product and 1 < count <= 999:
                return product, count
        return value, None

    async def add_item(self, name: str) -> dict[str, Any]:
        token = await self._token()
        endpoint = (
            f"{settings.samsung_food_web_base}"
            "/api/grpc-web/whisk.x.list.v1.ListAPI/SyncItems"
        )
        body = build_sync_items_add_request(
            self.list_id,
            name,
            int(time.time() * 1000),
        )

        headers = self._write_headers(token)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.post(endpoint, headers=headers, content=body)

        if response.status_code == 401:
            token = await self.auth.get_token(force_refresh=True)
            headers = self._write_headers(token)
            async with httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.post(endpoint, headers=headers, content=body)

        if response.status_code >= 400:
            raise SamsungFoodError(
                f"Samsung Food WRITE failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        parsed = parse_grpc_web_response(response.content)
        if parsed.grpc_status not in (None, 0):
            raise SamsungFoodError(
                f"Samsung Food gRPC error {parsed.grpc_status}: "
                f"{parsed.grpc_message or 'unknown'}"
            )

        printable = extract_printable_strings(parsed.message)
        samsung_item_id = next(
            (
                s for s in printable
                if len(s) >= 24 and all(c.isalnum() or c in "-_" for c in s)
            ),
            None,
        )

        return {
            "grpc_status": parsed.grpc_status,
            "grpc_message": parsed.grpc_message,
            "samsung_item_id": samsung_item_id,
        }

    async def _post_sync_items(self, body: bytes) -> dict[str, Any]:
        token = await self._token()
        endpoint = (
            f"{settings.samsung_food_web_base}"
            "/api/grpc-web/whisk.x.list.v1.ListAPI/SyncItems"
        )
        headers = self._write_headers(token)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.post(endpoint, headers=headers, content=body)

        if response.status_code == 401:
            token = await self.auth.get_token(force_refresh=True)
            headers = self._write_headers(token)
            async with httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.post(endpoint, headers=headers, content=body)

        if response.status_code >= 400:
            raise SamsungFoodError(
                f"Samsung Food WRITE failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        parsed = parse_grpc_web_response(response.content)
        if parsed.grpc_status not in (None, 0):
            raise SamsungFoodError(
                f"Samsung Food gRPC error {parsed.grpc_status}: "
                f"{parsed.grpc_message or 'unknown'}"
            )

        return {
            "grpc_status": parsed.grpc_status,
            "grpc_message": parsed.grpc_message,
        }

    async def _find_item(self, item_id: str) -> ShoppingItem:
        current = await self.get_list()
        wanted = item_id.replace("-", "").casefold()
        for item in current.items:
            if item.id and item.id.replace("-", "").casefold() == wanted:
                return item
        raise SamsungFoodError(f"Shopping item not found: {item_id}")

    async def set_item_checked(self, item_id: str, checked: bool) -> dict[str, Any]:
        item = await self._find_item(item_id)
        body = build_sync_items_checked_request(
            self.list_id,
            item_id,
            item.name,
            checked,
            int(time.time() * 1000),
            quantity=item.quantity,
            unit=item.unit,
        )
        result = await self._post_sync_items(body)
        # A successful gRPC write is the acknowledgement. Samsung's read side is
        # eventually consistent, so an immediate second list read adds latency
        # and can time out even though the checkbox was already saved.
        return result

    async def set_item_quantity(
        self,
        item_id: str,
        quantity: float,
        unit: str = "stk",
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise SamsungFoodError("Quantity must be greater than zero")
        item = await self._find_item(item_id)
        body = build_sync_items_quantity_request(
            self.list_id,
            item_id,
            item.name,
            bool(item.checked),
            quantity,
            unit.strip() or "stk",
            int(time.time() * 1000),
        )
        result = await self._post_sync_items(body)
        return result

    async def delete_item(self, item_id: str) -> dict[str, Any]:
        await self._find_item(item_id)
        body = build_sync_items_delete_request(
            self.list_id,
            item_id,
            int(time.time() * 1000),
        )
        result = await self._post_sync_items(body)

        current = await self.get_list()
        wanted = item_id.replace("-", "").casefold()
        if any(
            item.id and item.id.replace("-", "").casefold() == wanted
            for item in current.items
        ):
            raise SamsungFoodError(
                "Samsung accepted the delete SyncItems operation, but the item "
                "was still present on read-back"
            )
        return result

    def _write_headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/grpc-web+proto",
            "Cookie": f"whisk.USER_TOKEN={token}; _whsk=3",
            "Origin": settings.samsung_food_web_base,
            "Referer": (
                f"{settings.samsung_food_web_base}/shopping-list/{self.list_id}"
            ),
            "x-grpc-web": "1",
            "x-user-agent": "grpc-web-ts/1.0",
            "x-whisk-app-name": "webapp",
            "x-whisk-app-version": settings.samsung_food_app_version,
            "x-whisk-device-type": "Tablet",
        }

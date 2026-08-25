from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .households import HouseholdContext, read_household, require_household, update_household
from .product_identity import normalize


router = APIRouter(prefix="/api/mobile/v1/quick-add", tags=["quick-add"])
MAX_RANKED_ITEMS = 10
MINIMUM_PURCHASES = 3
MAX_COUNTED_ITEM_IDS = 5_000
PRUNED_COUNTED_ITEM_IDS = 4_000


class QuickAddItem(BaseModel):
    name: str
    purchase_count: int
    rank: int
    eligible: bool


class QuickAddResponse(BaseModel):
    ok: bool = True
    minimum_purchases: int = MINIMUM_PURCHASES
    items: list[QuickAddItem]


def ranked_items(household: dict[str, Any]) -> list[QuickAddItem]:
    raw_items = household.get("quick_add", {}).get("items", {})
    if not isinstance(raw_items, dict):
        return []

    candidates: list[tuple[str, dict[str, Any]]] = []
    for key, value in raw_items.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        name = value.get("name")
        count = value.get("purchase_count")
        if not isinstance(name, str) or not name.strip() or not isinstance(count, int) or count < 1:
            continue
        candidates.append((key, value))

    candidates.sort(
        key=lambda candidate: (
            -candidate[1]["purchase_count"],
            -int(candidate[1].get("last_purchased_at", 0)),
            candidate[0],
        )
    )
    return [
        QuickAddItem(
            name=value["name"],
            purchase_count=value["purchase_count"],
            rank=index,
            eligible=value["purchase_count"] >= MINIMUM_PURCHASES,
        )
        for index, (_, value) in enumerate(candidates[:MAX_RANKED_ITEMS], start=1)
    ]


def _prune_counted_item_ids(counted: dict[str, Any]) -> None:
    if len(counted) <= MAX_COUNTED_ITEM_IDS:
        return
    newest = sorted(
        counted.items(),
        key=lambda entry: int(entry[1].get("purchased_at", 0)) if isinstance(entry[1], dict) else 0,
        reverse=True,
    )[:PRUNED_COUNTED_ITEM_IDS]
    counted.clear()
    counted.update(newest)


async def record_purchase(
    context: HouseholdContext,
    *,
    item_id: str,
    item_name: str,
) -> bool:
    """Count one confirmed unchecked-to-checked transition per list item ID."""
    clean_name = " ".join(item_name.strip().split())
    key = normalize(clean_name)
    if not item_id or not clean_name or not key:
        return False
    purchased_at = int(time.time())

    def mutate(household: dict[str, Any]) -> bool:
        quick_add = household.setdefault("quick_add", {})
        items = quick_add.setdefault("items", {})
        counted = quick_add.setdefault("counted_item_ids", {})
        if item_id in counted:
            return False

        current = items.setdefault(key, {
            "name": clean_name,
            "purchase_count": 0,
            "first_purchased_at": purchased_at,
        })
        current["name"] = clean_name
        current["purchase_count"] = int(current.get("purchase_count", 0)) + 1
        current["last_purchased_at"] = purchased_at
        counted[item_id] = {"key": key, "purchased_at": purchased_at}
        _prune_counted_item_ids(counted)
        return True

    return bool(await update_household(context, mutate))


@router.get("", response_model=QuickAddResponse)
async def get_quick_add(
    context: HouseholdContext = Depends(require_household),
) -> QuickAddResponse:
    household = await read_household(context)
    return QuickAddResponse(items=ranked_items(household))

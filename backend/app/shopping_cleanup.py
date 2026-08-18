from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .samsung import SamsungFoodClient, SamsungFoodError
from .mobile_offer_metadata import (
    load_offer_metadata_store,
    offer_metadata_key,
    save_offer_metadata_store,
)
from .households import LEGACY_HOUSEHOLD_ID, legacy_worker_context, load_store as load_households, save_store as save_households


TIMEZONE = ZoneInfo("Europe/Copenhagen")


async def delete_checked_items() -> dict[str, int]:
    legacy_worker_context()
    client = SamsungFoodClient()
    current = await client.get_list()
    checked = [item for item in current.items if item.checked is True and item.id]
    deleted = 0
    failed = 0
    deleted_items: list[tuple[str, str]] = []
    for item in checked:
        try:
            await client.delete_item(item.id)
            deleted += 1
            deleted_items.append((item.id, item.name))
        except SamsungFoodError:
            failed += 1
    if deleted_items:
        metadata = load_offer_metadata_store()
        for item_id, name in deleted_items:
            metadata.pop(offer_metadata_key(name, item_id), None)
            metadata.pop(offer_metadata_key(name), None)
        save_offer_metadata_store(metadata)
    local_found, local_deleted = delete_checked_local_households()
    return {
        "found": len(checked) + local_found,
        "deleted": deleted + local_deleted,
        "failed": failed,
    }


def delete_checked_local_households() -> tuple[int, int]:
    store = load_households()
    found = deleted = 0
    for household_id, household in store.get("households", {}).items():
        if household_id == LEGACY_HOUSEHOLD_ID or household.get("list_backend") != "local":
            continue
        items = household.get("items", [])
        checked = [item for item in items if item.get("checked")]
        found += len(checked)
        deleted += len(checked)
        household["items"] = [item for item in items if not item.get("checked")]
    if deleted:
        save_households(store)
    return found, deleted


def seconds_until_next_midnight(now: datetime | None = None) -> float:
    current = now or datetime.now(TIMEZONE)
    tomorrow = current.date() + timedelta(days=1)
    target = datetime.combine(tomorrow, time.min, tzinfo=TIMEZONE)
    return max((target - current).total_seconds(), 1)


async def worker() -> None:
    while True:
        await asyncio.sleep(seconds_until_next_midnight())
        try:
            print({"shopping_cleanup": await delete_checked_items()}, flush=True)
        except Exception as exc:
            print({"shopping_cleanup_error": str(exc)}, flush=True)


if __name__ == "__main__":
    asyncio.run(worker())

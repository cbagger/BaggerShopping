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


TIMEZONE = ZoneInfo("Europe/Copenhagen")


async def delete_checked_items() -> dict[str, int]:
    client = SamsungFoodClient()
    current = await client.get_list()
    checked = [item for item in current.items if item.checked is True and item.id]
    deleted = 0
    failed = 0
    deleted_names: list[str] = []
    for item in checked:
        try:
            await client.delete_item(item.id)
            deleted += 1
            deleted_names.append(item.name)
        except SamsungFoodError:
            failed += 1
    if deleted_names:
        metadata = load_offer_metadata_store()
        for name in deleted_names:
            metadata.pop(offer_metadata_key(name), None)
        save_offer_metadata_store(metadata)
    return {"found": len(checked), "deleted": deleted, "failed": failed}


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

from __future__ import annotations

import asyncio
import os

from . import flyer_push as base
from .luna_member_coverage import notification_ready


# Detect retailer revisions more promptly without changing Luna spend. The base
# worker still de-duplicates releases and APNs deliveries exactly as before.
os.environ.setdefault("FLYER_PUSH_INTERVAL_SECONDS", "900")


def _quality_ready_records(records: list[dict]) -> list[dict]:
    return [record for record in records if notification_ready(record)]


async def deliver_ready_notifications() -> dict[str, int]:
    records = _quality_ready_records(base.ready_publication_records())
    return await base._deliver_ready_records(records)


def install_quality_gate() -> None:
    # check_and_send() and worker() both resolve this module global dynamically.
    # Replacing only the delivery function keeps discovery/readiness behaviour
    # untouched while preventing a premature "Ny tilbudsavis" notification.
    base.deliver_ready_notifications = deliver_ready_notifications


async def main() -> None:
    install_quality_gate()
    await base.worker()


if __name__ == "__main__":
    asyncio.run(main())

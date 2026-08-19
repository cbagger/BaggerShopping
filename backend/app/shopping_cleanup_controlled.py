from __future__ import annotations

import asyncio
from datetime import datetime

from . import shopping_cleanup
from .control_telemetry import write_heartbeat


async def _heartbeat_loop() -> None:
    while True:
        try:
            seconds = shopping_cleanup.seconds_until_next_midnight()
            write_heartbeat(
                "shopping-cleanup-worker",
                status="running",
                detail="Planlagt oprydning af købte varer ved næste midnat",
                metrics={
                    "seconds_until_next_run": round(seconds),
                    "next_run_in_hours": round(seconds / 3600, 2),
                    "timezone": str(shopping_cleanup.TIMEZONE),
                    "observed_at": datetime.now(shopping_cleanup.TIMEZONE).isoformat(),
                },
            )
        except Exception as exc:
            write_heartbeat("shopping-cleanup-worker", status="degraded", detail=str(exc))
        await asyncio.sleep(30)


async def main() -> None:
    write_heartbeat("shopping-cleanup-worker", status="starting", detail="Starter shopping cleanup-worker")
    heartbeat = asyncio.create_task(_heartbeat_loop())
    try:
        await shopping_cleanup.worker()
    except Exception as exc:
        write_heartbeat("shopping-cleanup-worker", status="error", detail=str(exc))
        raise
    finally:
        heartbeat.cancel()


if __name__ == "__main__":
    asyncio.run(main())

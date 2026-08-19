from __future__ import annotations

import asyncio

from . import flyer_push
from . import flyer_push_quality
from .control_telemetry import write_heartbeat
from .luna_member_coverage import status_payload as coverage_status


async def _heartbeat_loop() -> None:
    while True:
        try:
            store = flyer_push._load()
            devices = [
                value for value in store.get("devices", {}).values()
                if isinstance(value, dict) and value.get("enabled")
            ]
            write_heartbeat(
                "flyer-push-worker",
                status="running",
                detail="Overvåger nye avis-generationer og APNs-levering",
                metrics={
                    "enabled_devices": len(devices),
                    "last_provider_check_at": store.get("last_check_at"),
                    "last_ready_delivery_at": store.get("last_ready_delivery_at"),
                    "seen_publications": len(store.get("seen_publications", [])),
                    "coverage": coverage_status().get("counts", {}),
                },
            )
        except Exception as exc:
            write_heartbeat("flyer-push-worker", status="degraded", detail=str(exc))
        await asyncio.sleep(10)


async def main() -> None:
    write_heartbeat("flyer-push-worker", status="starting", detail="Starter flyer push quality-gate")
    heartbeat = asyncio.create_task(_heartbeat_loop())
    try:
        await flyer_push_quality.main()
    except Exception as exc:
        write_heartbeat("flyer-push-worker", status="error", detail=str(exc))
        raise
    finally:
        heartbeat.cancel()


if __name__ == "__main__":
    asyncio.run(main())

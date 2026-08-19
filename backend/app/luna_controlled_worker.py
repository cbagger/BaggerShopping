from __future__ import annotations

import asyncio

from . import luna_member_coverage
from . import luna_resilient_strong_worker as worker
from .control_telemetry import write_heartbeat
from .luna_enrichment import status_payload as luna_status
from .luna_resilient_worker import _load_quarantine


async def _heartbeat_loop() -> None:
    while True:
        try:
            coverage = luna_member_coverage.status_payload()
            luna = luna_status()
            pending = coverage.get("pending", [])
            focus = pending[0] if pending else None
            write_heartbeat(
                "luna-worker",
                status="running",
                detail=(
                    f"Analyserer {focus.get('retailer')} · {focus.get('title')}"
                    if isinstance(focus, dict)
                    else "Ingen obligatorisk avis-coverage i kø"
                ),
                metrics={
                    "coverage": coverage.get("counts", {}),
                    "focus": focus,
                    "usage": luna.get("usage", {}),
                    "records": luna.get("records", {}),
                    "quarantined": len(_load_quarantine()),
                },
            )
        except Exception as exc:
            write_heartbeat("luna-worker", status="degraded", detail=str(exc))
        await asyncio.sleep(5)


async def main() -> None:
    write_heartbeat("luna-worker", status="starting", detail="Starter Luna coverage-worker")
    heartbeat = asyncio.create_task(_heartbeat_loop())
    try:
        await worker.main()
    except Exception as exc:
        write_heartbeat("luna-worker", status="error", detail=str(exc))
        raise
    finally:
        heartbeat.cancel()


if __name__ == "__main__":
    asyncio.run(main())

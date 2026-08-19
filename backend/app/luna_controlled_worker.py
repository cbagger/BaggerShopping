from __future__ import annotations

import asyncio
import os
from typing import Any

from . import luna_member_coverage
from . import luna_resilient_strong_worker as worker
from .control_telemetry import write_heartbeat
from .luna_enrichment import status_payload as luna_status
from .luna_resilient_worker import _load_quarantine


_LAST_RESULT: dict[str, Any] = {}


def _heartbeat_payload() -> tuple[str, str, dict[str, Any]]:
    coverage = luna_member_coverage.status_payload()
    luna = luna_status()
    focus = _LAST_RESULT.get("coverage_focus")
    status = str(_LAST_RESULT.get("status") or "")

    if isinstance(focus, dict):
        detail = f"Analyserer {focus.get('retailer')} · {focus.get('title')}"
    elif status == "published":
        detail = f"Frigav source-avis {_LAST_RESULT.get('publication_id') or ''}".strip()
    elif status in {"budget-exhausted", "enrichment-budget-exhausted"}:
        detail = "Luna venter: månedsbudget er opbrugt"
    elif status in {"missing-api-key", "enrichment-missing-api-key"}:
        detail = "Luna venter: OpenAI API key mangler"
    elif status in {"disabled", "enrichment-disabled"}:
        detail = "Luna er deaktiveret i konfigurationen"
    else:
        detail = "Ingen obligatorisk avis-coverage i aktiv behandling"

    heartbeat_status = "running"
    if status in {"budget-exhausted", "enrichment-budget-exhausted", "missing-api-key", "enrichment-missing-api-key", "disabled", "enrichment-disabled"}:
        heartbeat_status = "degraded"

    metrics = {
        "worker_status": status or "starting",
        "coverage": coverage.get("counts", {}),
        "focus": focus if isinstance(focus, dict) else None,
        "usage": luna.get("usage", {}),
        "records": luna.get("records", {}),
        "quarantined": len(_load_quarantine()),
    }
    return heartbeat_status, detail, metrics


def _write_current_heartbeat() -> None:
    try:
        status, detail, metrics = _heartbeat_payload()
        write_heartbeat("luna-worker", status=status, detail=detail, metrics=metrics)
    except Exception as exc:
        write_heartbeat("luna-worker", status="degraded", detail=str(exc))


async def _heartbeat_loop() -> None:
    while True:
        _write_current_heartbeat()
        await asyncio.sleep(5)


async def main() -> None:
    global _LAST_RESULT
    write_heartbeat("luna-worker", status="starting", detail="Starter Luna coverage-worker")
    heartbeat = asyncio.create_task(_heartbeat_loop())

    idle_seconds = max(10, int(os.getenv("LUNA_QUEUE_POLL_SECONDS", "15")))
    progress_seconds = max(1, int(os.getenv("LUNA_RESILIENT_PROGRESS_SECONDS", "3")))
    pause_seconds = max(300, int(os.getenv("LUNA_QUEUE_ERROR_BACKOFF_SECONDS", "120")))

    try:
        while True:
            try:
                result = await worker.run_once()
                _LAST_RESULT = dict(result)
                worker.base.log.info("Luna controlled event: %s", result)
                _write_current_heartbeat()

                status = str(result.get("status") or "")
                focus = result.get("coverage_focus")
                if status == "published":
                    await asyncio.sleep(1)
                elif isinstance(focus, dict) and focus.get("status") == "pending":
                    await asyncio.sleep(progress_seconds)
                elif status == "enrichment-progress":
                    await asyncio.sleep(progress_seconds)
                elif status in {
                    "budget-exhausted",
                    "missing-api-key",
                    "disabled",
                    "enrichment-missing-api-key",
                    "enrichment-disabled",
                }:
                    await asyncio.sleep(pause_seconds)
                else:
                    await asyncio.sleep(idle_seconds)
            except Exception as exc:
                _LAST_RESULT = {"status": "error", "detail": str(exc)[:500]}
                write_heartbeat("luna-worker", status="error", detail=str(exc))
                worker.base.log.exception("Luna controlled worker failed")
                await asyncio.sleep(pause_seconds)
    finally:
        heartbeat.cancel()


if __name__ == "__main__":
    asyncio.run(main())

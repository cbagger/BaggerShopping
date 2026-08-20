from __future__ import annotations

import asyncio
import os
from typing import Any

from . import luna_member_coverage
from . import luna_resilient_strong_worker as worker
from .control_center_ops import append_event
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
    if status in {
        "budget-exhausted",
        "enrichment-budget-exhausted",
        "missing-api-key",
        "enrichment-missing-api-key",
        "disabled",
        "enrichment-disabled",
    }:
        heartbeat_status = "degraded"

    # Only operational facts are exported. The heartbeat deliberately never
    # contains API keys, Samsung credentials, APNs secrets or mobile tokens.
    metrics = {
        "worker_status": status or "starting",
        "enabled": bool(luna.get("enabled")),
        "apply_results": bool(luna.get("apply_results")),
        "model": luna.get("model"),
        "api_key_configured": bool(luna.get("api_key_configured")),
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


def _usage_delta(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[int, float, int, int, int, int, int]:
    before_usage = before.get("usage", {}) if isinstance(before.get("usage"), dict) else {}
    after_usage = after.get("usage", {}) if isinstance(after.get("usage"), dict) else {}
    requests = max(0, int(after_usage.get("requests") or 0) - int(before_usage.get("requests") or 0))
    cost = max(0.0, float(after_usage.get("estimated_cost_dkk") or 0.0) - float(before_usage.get("estimated_cost_dkk") or 0.0))
    input_tokens = max(0, int(after_usage.get("input_tokens") or 0) - int(before_usage.get("input_tokens") or 0))
    output_tokens = max(0, int(after_usage.get("output_tokens") or 0) - int(before_usage.get("output_tokens") or 0))
    cached_tokens = max(0, int(after_usage.get("cached_input_tokens") or 0) - int(before_usage.get("cached_input_tokens") or 0))
    cache_write_tokens = max(0, int(after_usage.get("cache_write_tokens") or 0) - int(before_usage.get("cache_write_tokens") or 0))
    uncached_tokens = max(0, int(after_usage.get("uncached_input_tokens") or 0) - int(before_usage.get("uncached_input_tokens") or 0))
    return requests, cost, input_tokens, output_tokens, cached_tokens, cache_write_tokens, uncached_tokens


def _record_openai_event(before: dict[str, Any], after: dict[str, Any], result: dict[str, Any]) -> None:
    (
        requests,
        cost,
        input_tokens,
        output_tokens,
        cached_tokens,
        cache_write_tokens,
        uncached_tokens,
    ) = _usage_delta(before, after)
    if requests <= 0:
        return
    focus = result.get("coverage_focus") if isinstance(result.get("coverage_focus"), dict) else {}
    retailer = str(focus.get("retailer") or result.get("retailer") or "") or None
    title = str(focus.get("title") or result.get("title") or "")
    target = " · ".join(part for part in (retailer, title) if part) or "Luna enrichment"
    work = []
    for key, label in (
        ("pages_processed", "sideaudit"),
        ("pricing_crops_processed", "priscrop"),
        ("fallback_processed", "fallback"),
        ("variant_crops_processed", "variantcrop"),
    ):
        count = int(result.get(key) or 0)
        if count:
            work.append(f"{count} {label}")
    work_detail = " + ".join(work)
    detail_parts = [target]
    if work_detail:
        detail_parts.append(work_detail)
    detail_parts.extend((f"+{input_tokens + output_tokens:,} tokens", f"+{cost:.4f} kr."))
    append_event(
        category="luna",
        event_type="openai_usage",
        title=f"OpenAI · {requests} request{'s' if requests != 1 else ''}",
        detail=" · ".join(detail_parts),
        severity="cost",
        component="luna-worker",
        retailer=retailer,
        publication_id=str(focus.get("publication_id") or result.get("publication_id") or "") or None,
        requests=requests,
        cost_dkk=cost,
        metadata={
            "input_tokens": input_tokens,
            "uncached_input_tokens": uncached_tokens,
            "cached_input_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "output_tokens": output_tokens,
            "worker_status": result.get("status"),
            "requests_attempted": int(result.get("requests_attempted") or 0),
            "pages_processed": int(result.get("pages_processed") or 0),
            "pricing_crops_processed": int(result.get("pricing_crops_processed") or 0),
            "fallback_processed": int(result.get("fallback_processed") or 0),
            "variant_crops_processed": int(result.get("variant_crops_processed") or 0),
        },
    )


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
                before = luna_status()
                result = await worker.run_once()
                after = luna_status()
                _LAST_RESULT = dict(result)
                worker.base.log.info("Luna controlled event: %s", result)
                _record_openai_event(before, after, _LAST_RESULT)
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
                append_event(category="system", event_type="worker_error", title="Luna worker fejl", detail=str(exc), severity="error", component="luna-worker")
                worker.base.log.exception("Luna controlled worker failed")
                await asyncio.sleep(pause_seconds)
    finally:
        heartbeat.cancel()


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import _original_fetch_all_publications as fetch_all_publications
from .luna_enrichment import analyze_candidate, collect_candidates, load_config, status_payload
# Install semantic invariants first, then the cost policy. Both patch the shared
# semantic module before the worker binds functions locally.
from . import luna_semantic_guards as _semantic_guards
_semantic_guards.install()
from . import luna_cost_policy as _cost_policy
_cost_policy.install()
from .luna_semantic_audit import (
    analyze_crop_candidate,
    analyze_page_audit,
    collect_crop_candidates,
    collect_page_audit_candidates,
    semantic_status_payload,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna")

# Legacy fallback for pages that cannot be visually audited. The primary path
# is compact visual page scout -> pricing/member crop only when necessary.
_PAID_REASONS = {
    "member-signal-without-safe-price",
    "member-price-needs-visual-verification",
    "member-price-missing-ordinary-price",
    "membership-fee-near-product-prices",
    "implausible-provider-reference-price",
}


def _paid_candidates(candidates):
    return [
        candidate for candidate in candidates
        if _PAID_REASONS.intersection(candidate.decision.reasons)
    ]


async def run_once() -> dict:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "disabled", **status_payload(), **semantic_status_payload(), "cost_policy": _cost_policy.status_payload()}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "missing-api-key", **status_payload(), **semantic_status_payload(), "cost_policy": _cost_policy.status_payload()}

    publications = await fetch_all_publications()

    total_limit = max(1, int(config.get("max_requests_per_scan", 20)))
    page_limit = max(
        1,
        int(config.get("max_page_audits_per_scan", max(1, total_limit // 2))),
    )
    crop_limit = max(1, int(config.get("max_crop_verifications_per_scan", 10)))

    page_candidates = collect_page_audit_candidates(publications)
    processed_pages = 0
    processed_crops = 0
    processed_fallback = 0
    stop_status: str | None = None

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        # Only pricing/member-critical rows become proactive crop candidates in
        # cost-first mode. Variant-only uncertainty remains a safe UI blocker
        # without spending a second request.
        initial_crop_candidates = collect_crop_candidates(publications)
        crop_budget = min(crop_limit, total_limit)
        for candidate in initial_crop_candidates[:crop_budget]:
            result = await analyze_crop_candidate(candidate, client=client)
            if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                stop_status = str(result.get("status"))
                break
            processed_crops += 1

        remaining = max(0, total_limit - processed_crops)
        if stop_status is None and remaining:
            for candidate in page_candidates[: min(page_limit, remaining)]:
                result = await analyze_page_audit(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_pages += 1

        remaining = max(0, total_limit - processed_pages - processed_crops)
        if stop_status is None and remaining:
            new_crop_candidates = collect_crop_candidates(publications)
            already = {candidate.fingerprint for candidate in initial_crop_candidates[:crop_budget]}
            fresh = [candidate for candidate in new_crop_candidates if candidate.fingerprint not in already]
            for candidate in fresh[: min(max(0, crop_limit - processed_crops), remaining)]:
                result = await analyze_crop_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_crops += 1

        # Compatibility fallback is kept for offers without usable page images.
        remaining = max(0, total_limit - processed_pages - processed_crops)
        fallback_candidates = _paid_candidates(collect_candidates(publications))
        if stop_status is None and remaining:
            for candidate in fallback_candidates[:remaining]:
                result = await analyze_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_fallback += 1

    processed = processed_pages + processed_crops + processed_fallback
    return {
        "status": stop_status or ("processed" if processed else "idle"),
        "page_audit_candidates": len(page_candidates),
        "page_audits_processed": processed_pages,
        "crop_candidates": len(collect_crop_candidates(publications)),
        "crops_processed": processed_crops,
        "fallback_candidates": len(_paid_candidates(collect_candidates(publications))),
        "fallback_processed": processed_fallback,
        "processed": processed,
        **status_payload(),
        **semantic_status_payload(),
        "cost_policy": _cost_policy.status_payload(),
    }


async def main() -> None:
    while True:
        try:
            result = await run_once()
            log.info("Luna cycle: %s", result)
        except Exception:
            log.exception("Luna cost-first semantic audit cycle failed")
        config = load_config()
        await asyncio.sleep(max(300, int(config.get("scan_interval_seconds", 3600))))


if __name__ == "__main__":
    asyncio.run(main())

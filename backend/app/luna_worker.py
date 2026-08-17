from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import _original_fetch_all_publications as fetch_all_publications
from .luna_enrichment import analyze_candidate, collect_candidates, load_config, status_payload
from .luna_semantic_audit import (
    analyze_crop_candidate,
    analyze_page_audit,
    collect_crop_candidates,
    collect_page_audit_candidates,
    semantic_status_payload,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna")

# Legacy fallback for pages that cannot be visually audited (for example a
# provider without a usable page image). The Build 58 primary path is now:
# full-page semantic audit -> targeted crop only when needed.
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
        return {"status": "disabled", **status_payload(), **semantic_status_payload()}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "missing-api-key", **status_payload(), **semantic_status_payload()}

    # Always scan raw deterministic source facts. Luna's cached overlays never
    # feed back into their own page fingerprints or candidate selection.
    publications = await fetch_all_publications()

    total_limit = max(1, int(config.get("max_requests_per_scan", 20)))
    page_limit = min(
        total_limit,
        max(1, int(config.get("max_page_audits_per_scan", total_limit))),
    )
    crop_limit = max(1, int(config.get("max_crop_verifications_per_scan", 10)))

    page_candidates = collect_page_audit_candidates(publications)
    processed_pages = 0
    processed_crops = 0
    processed_fallback = 0
    stop_status: str | None = None

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        for candidate in page_candidates[:page_limit]:
            result = await analyze_page_audit(candidate, client=client)
            if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                stop_status = str(result.get("status"))
                break
            processed_pages += 1

        remaining = max(0, total_limit - processed_pages)
        crop_candidates = collect_crop_candidates(publications)
        if stop_status is None and remaining:
            for candidate in crop_candidates[: min(crop_limit, remaining)]:
                result = await analyze_crop_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_crops += 1

        # Keep the old selective pricing verifier only as a compatibility
        # fallback for offers whose page could not be audited yet. It is never
        # the primary Build 58 decision mechanism.
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
    }


async def main() -> None:
    while True:
        try:
            result = await run_once()
            log.info("Luna cycle: %s", result)
        except Exception:
            # Luna is strictly isolated. A page-audit/OpenAI failure must never
            # terminate or degrade the deterministic Kurv backend.
            log.exception("Luna semantic audit cycle failed")
        config = load_config()
        await asyncio.sleep(max(300, int(config.get("scan_interval_seconds", 3600))))


if __name__ == "__main__":
    asyncio.run(main())

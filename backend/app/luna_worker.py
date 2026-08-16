from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import _original_fetch_all_publications as fetch_all_publications
from .luna_enrichment import analyze_candidate, collect_candidates, load_config, status_payload

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna")

# Cost-first policy for Luna v1. Normal low-quality offers and variant-only
# uncertainty remain on Kurv's free engines. When one of these price/member
# reasons already sends an offer to Luna, the same call still returns brand and
# variants for free reuse by other modules.
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
        return {"status": "disabled", **status_payload()}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "missing-api-key", **status_payload()}

    # Always scan raw deterministic source facts, not Luna's own cached overlay.
    # This prevents AI results from changing their own fingerprint/candidacy.
    publications = await fetch_all_publications()
    all_candidates = collect_candidates(publications)
    candidates = _paid_candidates(all_candidates)
    limit = max(1, int(config.get("max_requests_per_scan", 20)))
    selected = candidates[:limit]
    if not selected:
        return {
            "status": "idle",
            "candidates": 0,
            "deferred_free_engine_candidates": len(all_candidates),
            **status_payload(),
        }

    processed = 0
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        for candidate in selected:
            result = await analyze_candidate(candidate, client=client)
            if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                break
            processed += 1
    return {
        "status": "processed",
        "candidates": len(candidates),
        "processed": processed,
        "deferred_free_engine_candidates": max(0, len(all_candidates) - len(candidates)),
        **status_payload(),
    }


async def main() -> None:
    while True:
        try:
            result = await run_once()
            log.info("Luna cycle: %s", result)
        except Exception:
            # The worker is intentionally isolated. A Luna/OpenAI failure must
            # never terminate or degrade the normal Kurv backend.
            log.exception("Luna enrichment cycle failed")
        config = load_config()
        await asyncio.sleep(max(300, int(config.get("scan_interval_seconds", 3600))))


if __name__ == "__main__":
    asyncio.run(main())

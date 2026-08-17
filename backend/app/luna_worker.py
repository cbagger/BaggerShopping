from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import _original_fetch_all_publications as fetch_all_publications
from .luna_enrichment import analyze_candidate, collect_candidates, load_config, status_payload
# Install semantic invariants first, then the quality/cost policy. Both patch the
# shared semantic module before the worker binds functions locally.
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

# Legacy fallback for pages that cannot be visually audited. Rich page audit is
# primary; this fallback remains restricted to pricing/member-critical cases.
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


def _split_crop_candidates(candidates):
    ordered = _cost_policy.sort_crop_candidates(candidates)
    pricing = [candidate for candidate in ordered if not _cost_policy.is_variant_only_crop(candidate)]
    variants = [candidate for candidate in ordered if _cost_policy.is_variant_only_crop(candidate)]
    return pricing, variants


async def run_once() -> dict:
    config = load_config()
    if not config.get("enabled"):
        return {
            "status": "disabled",
            **status_payload(),
            **semantic_status_payload(),
            "cost_policy": _cost_policy.status_payload(),
        }
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {
            "status": "missing-api-key",
            **status_payload(),
            **semantic_status_payload(),
            "cost_policy": _cost_policy.status_payload(),
        }

    publications = await fetch_all_publications()

    total_limit = max(1, int(config.get("max_requests_per_scan", 20)))
    page_limit = max(
        1,
        int(config.get("max_page_audits_per_scan", max(1, total_limit // 2))),
    )
    crop_limit = max(1, int(config.get("max_crop_verifications_per_scan", 10)))
    variant_limit = max(0, int(config.get("max_variant_crops_per_scan", 5)))

    page_candidates = collect_page_audit_candidates(publications)
    processed_pages = 0
    processed_crops = 0
    processed_variant_crops = 0
    processed_fallback = 0
    processed_crop_fingerprints: set[str] = set()
    stop_status: str | None = None

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        # 1) Finish already-pending price/member verification before optional
        # work. Variant-only candidates are deliberately held back.
        initial_pricing, _ = _split_crop_candidates(collect_crop_candidates(publications))
        for candidate in initial_pricing[: min(crop_limit, total_limit)]:
            result = await analyze_crop_candidate(candidate, client=client)
            if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                stop_status = str(result.get("status"))
                break
            processed_crops += 1
            processed_crop_fingerprints.add(candidate.fingerprint)

        # 2) Rich page audits are the main value-producing pass and run before
        # optional variant enrichment.
        remaining = max(0, total_limit - processed_crops)
        if stop_status is None and remaining:
            for candidate in page_candidates[: min(page_limit, remaining)]:
                result = await analyze_page_audit(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_pages += 1

        # 3) Resolve any new price/member crops created by those page audits.
        remaining = max(0, total_limit - processed_pages - processed_crops)
        crop_slots = max(0, crop_limit - processed_crops)
        if stop_status is None and remaining and crop_slots:
            fresh_pricing, _ = _split_crop_candidates(collect_crop_candidates(publications))
            fresh_pricing = [
                candidate
                for candidate in fresh_pricing
                if candidate.fingerprint not in processed_crop_fingerprints
            ]
            for candidate in fresh_pricing[: min(crop_slots, remaining)]:
                result = await analyze_crop_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_crops += 1
                processed_crop_fingerprints.add(candidate.fingerprint)

        # 4) Compatibility fallback is pricing-critical too, so it remains
        # ahead of optional variant crops.
        remaining = max(0, total_limit - processed_pages - processed_crops - processed_fallback)
        fallback_candidates = _paid_candidates(collect_candidates(publications))
        if stop_status is None and remaining:
            for candidate in fallback_candidates[:remaining]:
                result = await analyze_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_fallback += 1

        # 5) Spend the remaining cycle/month slice on genuine variant gaps.
        # This is the first work to stop as budget tightens.
        remaining = max(
            0,
            total_limit - processed_pages - processed_crops - processed_fallback,
        )
        crop_slots = max(0, crop_limit - processed_crops)
        if (
            stop_status is None
            and remaining
            and crop_slots
            and variant_limit
            and _cost_policy.variant_crop_budget_allows(config)
        ):
            _, variant_candidates = _split_crop_candidates(collect_crop_candidates(publications))
            variant_candidates = [
                candidate
                for candidate in variant_candidates
                if candidate.fingerprint not in processed_crop_fingerprints
            ]
            for candidate in variant_candidates[: min(variant_limit, crop_slots, remaining)]:
                if not _cost_policy.variant_crop_budget_allows(config):
                    break
                result = await analyze_crop_candidate(candidate, client=client)
                if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                    stop_status = str(result.get("status"))
                    break
                processed_crops += 1
                processed_variant_crops += 1
                processed_crop_fingerprints.add(candidate.fingerprint)

    processed = processed_pages + processed_crops + processed_fallback
    current_crops = collect_crop_candidates(publications)
    _, current_variant_crops = _split_crop_candidates(current_crops)
    return {
        "status": stop_status or ("processed" if processed else "idle"),
        "page_audit_candidates": len(page_candidates),
        "page_audits_processed": processed_pages,
        "crop_candidates": len(current_crops),
        "variant_crop_candidates": len(current_variant_crops),
        "crops_processed": processed_crops,
        "variant_crops_processed": processed_variant_crops,
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
            log.exception("Luna quality-first semantic audit cycle failed")
        config = load_config()
        await asyncio.sleep(max(300, int(config.get("scan_interval_seconds", 3600))))


if __name__ == "__main__":
    asyncio.run(main())

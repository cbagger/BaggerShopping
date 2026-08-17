from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import _original_fetch_all_publications as fetch_all_publications
from .flyer_readiness import (
    mark_failed,
    mark_processing_attempt,
    mark_ready,
    pending_publication_records,
    publication_fingerprint,
    status_payload as readiness_status_payload,
)
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

# Legacy fallback for offers that cannot be covered by a usable page image.
# Rich page audit remains primary; fallback stays pricing/member-critical only.
_PAID_REASONS = {
    "member-signal-without-safe-price",
    "member-price-needs-visual-verification",
    "member-price-missing-ordinary-price",
    "membership-fee-near-product-prices",
    "implausible-provider-reference-price",
}
_STOP_STATUSES = {"budget-exhausted", "disabled", "missing-api-key"}


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
    """Manual broad maintenance cycle retained for diagnostics/backfill.

    The persistent worker no longer calls this on a timer. Normal production
    uses ``run_queued_once`` and therefore does not refetch every retailer when
    there is no new/changed flyer waiting for Luna.
    """
    config = load_config()
    if not config.get("enabled"):
        return {
            "status": "disabled",
            **status_payload(),
            **semantic_status_payload(),
            "readiness": readiness_status_payload(),
            "cost_policy": _cost_policy.status_payload(),
        }
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {
            "status": "missing-api-key",
            **status_payload(),
            **semantic_status_payload(),
            "readiness": readiness_status_payload(),
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
        initial_pricing, _ = _split_crop_candidates(collect_crop_candidates(publications))
        for candidate in initial_pricing[: min(crop_limit, total_limit)]:
            result = await analyze_crop_candidate(candidate, client=client)
            if result.get("status") in _STOP_STATUSES:
                stop_status = str(result.get("status"))
                break
            processed_crops += 1
            processed_crop_fingerprints.add(candidate.fingerprint)

        remaining = max(0, total_limit - processed_crops)
        if stop_status is None and remaining:
            for candidate in page_candidates[: min(page_limit, remaining)]:
                result = await analyze_page_audit(candidate, client=client)
                if result.get("status") in _STOP_STATUSES:
                    stop_status = str(result.get("status"))
                    break
                processed_pages += 1

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
                if result.get("status") in _STOP_STATUSES:
                    stop_status = str(result.get("status"))
                    break
                processed_crops += 1
                processed_crop_fingerprints.add(candidate.fingerprint)

        remaining = max(0, total_limit - processed_pages - processed_crops - processed_fallback)
        fallback_candidates = _paid_candidates(collect_candidates(publications))
        if stop_status is None and remaining:
            for candidate in fallback_candidates[:remaining]:
                result = await analyze_candidate(candidate, client=client)
                if result.get("status") in _STOP_STATUSES:
                    stop_status = str(result.get("status"))
                    break
                processed_fallback += 1

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
                if result.get("status") in _STOP_STATUSES:
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
        "readiness": readiness_status_payload(),
        "cost_policy": _cost_policy.status_payload(),
    }


async def _process_publication(record: dict) -> dict:
    """Fully process one queued flyer version, then atomically publish it."""
    config = load_config()
    publication_id = str(record.get("publication_id") or "")
    expected_fingerprint = str(record.get("fingerprint") or "")

    if not config.get("enabled"):
        return {"status": "disabled", "publication_id": publication_id}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "missing-api-key", "publication_id": publication_id}
    if not publication_id or not expected_fingerprint:
        return {"status": "invalid-queue-record", "publication_id": publication_id}

    publications = await fetch_all_publications()
    publication = next(
        (
            value for value in publications
            if value.id == publication_id and value.status != "expired"
        ),
        None,
    )
    if publication is None:
        mark_failed(publication_id, expected_fingerprint, "publication-not-found")
        return {"status": "publication-not-found", "publication_id": publication_id}

    actual_fingerprint = publication_fingerprint(publication)
    if actual_fingerprint != expected_fingerprint:
        # Discovery will enqueue the newer provider version. Never publish a
        # version different from the one whose processing job we accepted.
        mark_failed(publication_id, expected_fingerprint, "publication-version-changed")
        return {
            "status": "publication-version-changed",
            "publication_id": publication_id,
        }

    mark_processing_attempt(publication_id, expected_fingerprint)
    processed_pages = 0
    processed_pricing_crops = 0
    processed_variant_crops = 0
    processed_fallback = 0

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        # Rich audit every still-unprocessed page in THIS publication. There is
        # no hourly/broad scan and no cross-retailer work while this event runs.
        page_candidates = collect_page_audit_candidates([publication])
        for candidate in page_candidates:
            result = await analyze_page_audit(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                mark_failed(publication_id, expected_fingerprint, status)
                return {"status": status, "publication_id": publication_id}
            if status != "completed":
                error = str(result.get("error") or status or "page-audit-failed")
                mark_failed(publication_id, expected_fingerprint, error)
                return {
                    "status": "page-audit-failed",
                    "publication_id": publication_id,
                    "error": error,
                }
            processed_pages += 1

        # Price/member safety is mandatory before publication. A no-change crop
        # is NOT enough for a row the page audit explicitly deemed unsafe.
        pricing_candidates, _ = _split_crop_candidates(collect_crop_candidates([publication]))
        for candidate in pricing_candidates:
            result = await analyze_crop_candidate(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                mark_failed(publication_id, expected_fingerprint, status)
                return {"status": status, "publication_id": publication_id}
            if status != "completed":
                error = str(result.get("error") or status or "pricing-crop-unresolved")
                mark_failed(publication_id, expected_fingerprint, error)
                return {
                    "status": "pricing-crop-unresolved",
                    "publication_id": publication_id,
                    "error": error,
                }
            processed_pricing_crops += 1

        # Offers without a usable page image keep the old narrow verification
        # fallback. It has already been restricted to price/member-critical rows.
        fallback_candidates = _paid_candidates(collect_candidates([publication]))
        for candidate in fallback_candidates:
            result = await analyze_candidate(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                mark_failed(publication_id, expected_fingerprint, status)
                return {"status": status, "publication_id": publication_id}
            if status not in {"completed", "no-change"}:
                error = str(result.get("error") or status or "fallback-failed")
                mark_failed(publication_id, expected_fingerprint, error)
                return {
                    "status": "fallback-failed",
                    "publication_id": publication_id,
                    "error": error,
                }
            processed_fallback += 1

        # Variant enrichment is quality-first but remains safe-to-degrade. Try
        # every genuine gap while its separate monthly slice is available. If
        # that optional slice is exhausted, multiple_products still blocks
        # unsafe direct-add and the flyer can be published safely.
        _, variant_candidates = _split_crop_candidates(collect_crop_candidates([publication]))
        for candidate in variant_candidates:
            if not _cost_policy.variant_crop_budget_allows(config):
                break
            result = await analyze_crop_candidate(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                if status == "budget-exhausted":
                    break
                mark_failed(publication_id, expected_fingerprint, status)
                return {"status": status, "publication_id": publication_id}
            processed_variant_crops += 1

    # Mandatory work must be completely drained. Variant-only rows may remain
    # if their optional slice is exhausted; their UI state is conservative.
    remaining_pages = collect_page_audit_candidates([publication])
    remaining_pricing, remaining_variants = _split_crop_candidates(
        collect_crop_candidates([publication])
    )
    if remaining_pages or remaining_pricing:
        error = f"mandatory-work-remains pages={len(remaining_pages)} pricing={len(remaining_pricing)}"
        mark_failed(publication_id, expected_fingerprint, error)
        return {
            "status": "mandatory-work-remains",
            "publication_id": publication_id,
            "page_candidates": len(remaining_pages),
            "pricing_candidates": len(remaining_pricing),
        }

    if not mark_ready(publication):
        return {"status": "publication-version-changed", "publication_id": publication_id}

    return {
        "status": "ready",
        "publication_id": publication_id,
        "retailer": publication.retailer,
        "title": publication.title,
        "pages_processed": processed_pages,
        "pricing_crops_processed": processed_pricing_crops,
        "variant_crops_processed": processed_variant_crops,
        "fallback_processed": processed_fallback,
        "optional_variant_candidates_remaining": len(remaining_variants),
    }


async def run_queued_once() -> dict:
    """Process at most one locally queued publication event."""
    pending = pending_publication_records()
    if not pending:
        return {
            "status": "idle",
            "queue_depth": 0,
            "readiness": readiness_status_payload(),
        }
    result = await _process_publication(pending[0])
    return {
        **result,
        "queue_depth": len(pending_publication_records()),
        "readiness": readiness_status_payload(),
        "cost_policy": _cost_policy.status_payload(),
    }


async def main() -> None:
    # Queue polling is local /data only; it does NOT poll retailers or OpenAI.
    # The flyer-push detector creates jobs when a provider version changes.
    idle_seconds = max(5, int(os.getenv("LUNA_QUEUE_POLL_SECONDS", "15")))
    error_seconds = max(30, int(os.getenv("LUNA_QUEUE_ERROR_BACKOFF_SECONDS", "120")))

    while True:
        try:
            result = await run_queued_once()
            log.info("Luna publication event: %s", result)
            status = str(result.get("status") or "")
            if status == "idle":
                await asyncio.sleep(idle_seconds)
            elif status in {"budget-exhausted", "missing-api-key", "disabled"}:
                await asyncio.sleep(max(300, error_seconds))
            elif status == "ready":
                # Drain another queued flyer immediately.
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(error_seconds)
        except Exception:
            log.exception("Luna event-driven publication processing failed")
            await asyncio.sleep(error_seconds)


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import httpx

from .flyer_publications import fetch_raw_publications as fetch_all_publications
from .flyer_readiness import (
    STORE_VERSION as READINESS_STORE_VERSION,
    mark_failed,
    mark_processing_attempt,
    mark_ready,
    pending_publication_records,
    publication_fingerprint,
    readiness_store_version,
    status_payload as readiness_status_payload,
)
from .luna_enrichment import (
    analyze_candidate,
    collect_candidates,
    load_config,
    load_store,
    save_store,
    status_payload,
)
from . import luna_cost_policy as _cost_policy
from . import luna_semantic_guards as _semantic_guards
from .luna_semantic_engine import (
    analyze_crop_candidate,
    analyze_page_audit,
    collect_crop_candidates,
    collect_page_audit_candidates,
    offer_key,
    semantic_status_payload,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna")

_PAID_REASONS = {
    "member-signal-without-safe-price",
    "member-price-needs-visual-verification",
    "member-price-missing-ordinary-price",
    "membership-fee-near-product-prices",
    "implausible-provider-reference-price",
}
_STOP_STATUSES = {"budget-exhausted", "disabled", "missing-api-key"}


def _execution_lock_path() -> Path:
    return Path(os.getenv("LUNA_EXECUTION_LOCK_PATH", "/data/luna-execution.lock"))


def _stalled_publications_path() -> Path:
    return Path(os.getenv("LUNA_STALLED_PUBLICATIONS_PATH", "/data/luna-stalled-publications.json"))


def _stall_key(record: dict) -> str:
    return "|".join(
        (
            str(record.get("publication_id") or ""),
            str(record.get("fingerprint") or ""),
            str(record.get("processing_started_at") or ""),
        )
    )


def _load_stalled_publications() -> dict[str, dict]:
    path = _stalled_publications_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    rows = value.get("stalled")
    if not isinstance(rows, dict):
        return {}
    return {
        str(key): dict(row)
        for key, row in rows.items()
        if isinstance(row, dict)
    }


def _record_is_stalled(record: dict) -> bool:
    return _stall_key(record) in _load_stalled_publications()


def _mark_stalled_record(record: dict, error: str, fingerprints=()) -> None:
    path = _stalled_publications_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _load_stalled_publications()
    key = _stall_key(record)
    rows[key] = {
        "publication_id": str(record.get("publication_id") or ""),
        "fingerprint": str(record.get("fingerprint") or ""),
        "processing_started_at": record.get("processing_started_at"),
        "error": str(error)[:500],
        "candidate_fingerprints": sorted({str(value) for value in fingerprints if str(value)}),
    }
    if len(rows) > 500:
        rows = dict(list(rows.items())[-500:])
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(
        json.dumps({"version": 1, "stalled": rows}, ensure_ascii=False, separators=(",", ":")),
        "utf-8",
    )
    tmp.replace(path)


@contextmanager
def _execution_lease():
    """Prevent two Luna processes from spending/writing concurrently.

    The normal worker and diagnostic CLI commands can otherwise overlap because
    `docker exec` starts a second Python process inside the same container.
    """
    path = _execution_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def _candidate_page(candidate) -> int | None:
    page = getattr(candidate, "page_number", None)
    if isinstance(page, int) and page > 0:
        return page
    offer = getattr(candidate, "offer", None)
    page = getattr(offer, "page_number", None)
    return page if isinstance(page, int) and page > 0 else None


def _scope_to_changed_pages(candidates, changed_pages: set[int]):
    if not changed_pages:
        return []
    return [
        candidate for candidate in candidates
        if _candidate_page(candidate) is None or _candidate_page(candidate) in changed_pages
    ]


def _record_changed_pages(record: dict, publication) -> set[int]:
    result: set[int] = set()
    for value in record.get("changed_pages", []):
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < page <= int(publication.page_count or 0):
            result.add(page)
    return result


def _mandatory_pricing_crop_verified(result: dict, config: dict, offer=None) -> bool:
    """Mandatory crop completion is about pricing, not merely identity."""
    if str(result.get("status") or "") != "completed":
        return False
    facts = result.get("semantic_facts")
    if not isinstance(facts, dict) or not facts.get("visible"):
        return False
    if offer is not None:
        return _semantic_guards.mandatory_pricing_crop_resolved(offer, facts, config)

    threshold = float(config.get("min_apply_confidence", 0.96))
    if float(facts.get("pricing_confidence") or 0) < threshold:
        return False

    ordinary = facts.get("ordinary_price")
    member = facts.get("member_price")
    if ordinary is None and member is None:
        return False
    if ordinary is not None and (
        not isinstance(ordinary, (int, float))
        or isinstance(ordinary, bool)
        or float(ordinary) <= 0
    ):
        return False
    if member is not None and (
        not isinstance(member, (int, float))
        or isinstance(member, bool)
        or float(member) <= 0
    ):
        return False
    if member is not None and ordinary is None:
        return False
    if member is not None and ordinary is not None and float(member) >= float(ordinary):
        return False
    return True


def _requeue_mandatory_crop(candidate, previous_semantic: dict | None, error: str) -> None:
    store = load_store()
    semantic = store.setdefault("semantic_facts", {})
    if isinstance(previous_semantic, dict):
        semantic[offer_key(candidate.offer)] = previous_semantic

    record = store.setdefault("records", {}).get(candidate.fingerprint)
    if isinstance(record, dict):
        record["status"] = "failed"
        record["error"] = error[:500]

    pricing_index = store.setdefault("pricing_index", {})
    for signature, fingerprint in list(pricing_index.items()):
        if fingerprint == candidate.fingerprint:
            pricing_index.pop(signature, None)
    save_store(store)


async def _run_once_unlocked() -> dict:
    """Manual broad maintenance cycle retained for diagnostics/backfill."""
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

        remaining = max(0, total_limit - processed_pages - processed_crops)
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


async def run_once() -> dict:
    with _execution_lease() as acquired:
        if not acquired:
            return {
                "status": "busy",
                **status_payload(),
                "readiness": readiness_status_payload(),
            }
        return await _run_once_unlocked()


async def _process_publication(record: dict) -> dict:
    """Process only the source pages that changed, then atomically publish."""
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
        mark_failed(publication_id, expected_fingerprint, "publication-version-changed")
        return {
            "status": "publication-version-changed",
            "publication_id": publication_id,
        }

    changed_pages = _record_changed_pages(record, publication)
    mark_processing_attempt(publication_id, expected_fingerprint)
    processed_pages = 0
    processed_pricing_crops = 0
    processed_variant_crops = 0
    processed_fallback = 0
    processed_pricing_fingerprints: set[str] = set()

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        page_candidates = _scope_to_changed_pages(
            collect_page_audit_candidates([publication]), changed_pages
        )
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

        pricing_candidates, _ = _split_crop_candidates(
            _scope_to_changed_pages(collect_crop_candidates([publication]), changed_pages)
        )
        for candidate in pricing_candidates:
            before_store = load_store()
            previous_semantic = before_store.get("semantic_facts", {}).get(
                offer_key(candidate.offer)
            )
            result = await analyze_crop_candidate(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                mark_failed(publication_id, expected_fingerprint, status)
                return {"status": status, "publication_id": publication_id}
            if not _mandatory_pricing_crop_verified(result, config, candidate.offer):
                error = str(result.get("error") or status or "pricing-crop-unresolved")
                _requeue_mandatory_crop(candidate, previous_semantic, error)
                mark_failed(publication_id, expected_fingerprint, error)
                _mark_stalled_record(record, error, [candidate.fingerprint])
                return {
                    "status": "pricing-crop-unresolved",
                    "publication_id": publication_id,
                    "error": error,
                    "stalled": True,
                }
            processed_pricing_crops += 1
            processed_pricing_fingerprints.add(candidate.fingerprint)

        fallback_candidates = _scope_to_changed_pages(
            _paid_candidates(collect_candidates([publication])), changed_pages
        )
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

        _, variant_candidates = _split_crop_candidates(
            _scope_to_changed_pages(collect_crop_candidates([publication]), changed_pages)
        )
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

    remaining_pages = _scope_to_changed_pages(
        collect_page_audit_candidates([publication]), changed_pages
    )
    remaining_pricing, remaining_variants = _split_crop_candidates(
        _scope_to_changed_pages(collect_crop_candidates([publication]), changed_pages)
    )
    remaining_pricing_fingerprints = {
        candidate.fingerprint for candidate in remaining_pricing
    }
    repeated_pricing = processed_pricing_fingerprints.intersection(
        remaining_pricing_fingerprints
    )
    if repeated_pricing:
        error = (
            "mandatory-work-stalled "
            f"pricing={len(repeated_pricing)} total={len(remaining_pricing)}"
        )
        mark_failed(publication_id, expected_fingerprint, error)
        _mark_stalled_record(record, error, repeated_pricing)
        return {
            "status": "mandatory-work-stalled",
            "publication_id": publication_id,
            "pricing_candidates": len(remaining_pricing),
            "repeated_pricing_candidates": len(repeated_pricing),
            "stalled": True,
        }
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
        "changed_pages": sorted(changed_pages),
        "pages_processed": processed_pages,
        "pricing_crops_processed": processed_pricing_crops,
        "variant_crops_processed": processed_variant_crops,
        "fallback_processed": processed_fallback,
        "optional_variant_candidates_remaining": len(remaining_variants),
    }


async def _run_queued_once_unlocked() -> dict:
    """Process at most one locally queued publication event."""
    if readiness_store_version() < READINESS_STORE_VERSION:
        return {
            "status": "readiness-migration-pending",
            "queue_depth": 0,
            "readiness": readiness_status_payload(),
        }

    pending_all = pending_publication_records()
    if not pending_all:
        return {
            "status": "idle",
            "queue_depth": 0,
            "readiness": readiness_status_payload(),
        }

    pending = [record for record in pending_all if not _record_is_stalled(record)]
    if not pending:
        return {
            "status": "stalled",
            "queue_depth": len(pending_all),
            "stalled_publications": len(pending_all),
            "readiness": readiness_status_payload(),
        }

    result = await _process_publication(pending[0])
    return {
        **result,
        "queue_depth": len(pending_publication_records()),
        "readiness": readiness_status_payload(),
        "cost_policy": _cost_policy.status_payload(),
    }


async def run_queued_once() -> dict:
    with _execution_lease() as acquired:
        if not acquired:
            return {
                "status": "busy",
                "queue_depth": len(pending_publication_records()),
                "readiness": readiness_status_payload(),
            }
        return await _run_queued_once_unlocked()


async def main() -> None:
    idle_seconds = max(5, int(os.getenv("LUNA_QUEUE_POLL_SECONDS", "15")))
    error_seconds = max(30, int(os.getenv("LUNA_QUEUE_ERROR_BACKOFF_SECONDS", "120")))

    while True:
        try:
            result = await run_queued_once()
            log.info("Luna publication event: %s", result)
            status = str(result.get("status") or "")
            if status in {"idle", "busy", "readiness-migration-pending"}:
                await asyncio.sleep(idle_seconds)
            elif status == "stalled":
                await asyncio.sleep(max(300, error_seconds))
            elif status in {"budget-exhausted", "missing-api-key", "disabled"}:
                await asyncio.sleep(max(300, error_seconds))
            elif status == "ready":
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(error_seconds)
        except Exception:
            log.exception("Luna event-driven publication processing failed")
            await asyncio.sleep(error_seconds)


if __name__ == "__main__":
    asyncio.run(main())

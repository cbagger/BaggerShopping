from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx

from .flyer_publications import fetch_raw_publications as fetch_all_publications
from .flyer_readiness import (
    STORE_VERSION as READINESS_STORE_VERSION,
    mark_failed,
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
    status_payload,
)
from . import luna_cost_policy as _cost_policy
from .luna_semantic_engine import (
    analyze_crop_candidate,
    analyze_page_audit,
    collect_crop_candidates,
    collect_page_audit_candidates,
    offer_key,
    semantic_status_payload,
)
from .luna_worker import (
    _STOP_STATUSES,
    _execution_lease,
    _mandatory_pricing_crop_verified,
    _paid_candidates,
    _requeue_mandatory_crop,
    _split_crop_candidates,
    _stalled_publications_path,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna-resilient")

# A source publication must never be held back by an additive AI layer. Luna may
# improve individual offers after publication, but any unresolved offer fails
# closed to provider data and can never stall the whole flyer.
RESILIENCE_CONTRACT_VERSION = "publication-fail-open-offer-fail-closed-v1"


def _quarantine_path() -> Path:
    return Path(os.getenv("LUNA_QUARANTINE_PATH", "/data/luna-quarantined-work.json"))


def _load_quarantine() -> dict[str, dict]:
    path = _quarantine_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = value.get("items") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {
        str(key): dict(row)
        for key, row in rows.items()
        if isinstance(row, dict)
    }


def _save_quarantine(rows: dict[str, dict]) -> None:
    path = _quarantine_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(rows) > 4000:
        rows = dict(list(rows.items())[-4000:])
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "contract": RESILIENCE_CONTRACT_VERSION,
                "items": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "utf-8",
    )
    temporary.replace(path)


def _candidate_fingerprint(candidate) -> str:
    return str(getattr(candidate, "fingerprint", "") or "")


def _source_fingerprint(publication) -> str:
    return publication_fingerprint(publication)


def _quarantine_key(kind: str, publication, candidate) -> str:
    return "|".join(
        (
            RESILIENCE_CONTRACT_VERSION,
            str(kind),
            str(publication.id),
            _source_fingerprint(publication),
            _candidate_fingerprint(candidate),
        )
    )


def _is_quarantined(kind: str, publication, candidate) -> bool:
    return _quarantine_key(kind, publication, candidate) in _load_quarantine()


def _quarantine(kind: str, publication, candidate, reason: str) -> None:
    rows = _load_quarantine()
    key = _quarantine_key(kind, publication, candidate)
    offer = getattr(candidate, "offer", None)
    rows[key] = {
        "contract": RESILIENCE_CONTRACT_VERSION,
        "kind": str(kind),
        "publication_id": str(publication.id),
        "publication_fingerprint": _source_fingerprint(publication),
        "candidate_fingerprint": _candidate_fingerprint(candidate),
        "offer_id": str(getattr(offer, "id", "") or ""),
        "product_name": str(getattr(offer, "product_name", "") or ""),
        "page_number": getattr(offer, "page_number", None),
        "reason": str(reason)[:500],
    }
    _save_quarantine(rows)


def _clear_legacy_publication_stall(publication_id: str) -> int:
    """Retire old publication-wide stalls after source publication is released.

    PR #91's stall store prevented request loops, but publication-wide stalls are
    obsolete once failures are isolated per candidate. Removing them here also
    self-heals the current Lidl/365 state without manual JSON surgery.
    """
    path = _stalled_publications_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    rows = value.get("stalled") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        return 0

    kept = {
        key: row
        for key, row in rows.items()
        if not (
            isinstance(row, dict)
            and str(row.get("publication_id") or "") == str(publication_id)
        )
    }
    removed = len(rows) - len(kept)
    if not removed:
        return 0

    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {"version": 1, "stalled": kept},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "utf-8",
    )
    temporary.replace(path)
    return removed


def _provider_unit_equivalence(candidate) -> bool:
    """Skip a paid crop when provider text already proves equal unit/package basis.

    Examples: 1 kg at 59 kr naturally has a unit price of 59 kr/kg; one cucumber
    at 9 kr naturally has a per-piece price of 9 kr/stk. These are identities,
    not pricing-role conflicts. This rule only suppresses the ordinary-price
    unit collision; genuine member-price signals remain eligible for review.
    """
    offer = getattr(candidate, "offer", None)
    if offer is None or not isinstance(getattr(offer, "price", None), (int, float)):
        return False

    reasons = {str(value) for value in getattr(candidate, "reasons", ()) if str(value)}
    if "page-audit-ordinary-price-is-unit-price" not in reasons:
        return False
    hard_reasons = reasons - {
        "page-audit-ordinary-price-is-unit-price",
        "page-audit-variant-enrichment",
    }
    if hard_reasons:
        return False

    price = float(offer.price)
    escaped = re.escape(f"{price:.2f}".replace(".", ","))
    whole = str(int(price)) if price.is_integer() else None
    number = rf"(?:{escaped}" + (rf"|{re.escape(whole)}(?:[,.]00)?" if whole else "") + r")"
    text = " ".join(str(getattr(offer, "raw_text", "") or "").split()).casefold()

    exact_kg = bool(re.search(r"(?:\b1(?:[,.]0+)?\s*kg\b|\b1000\s*g\b)", text))
    exact_l = bool(re.search(r"(?:\b1(?:[,.]0+)?\s*(?:l|liter)\b|\b1000\s*ml\b)", text))
    exact_piece = bool(re.search(r"\b(?:1\s*stk\.?|pr\.?\s*stk\.?)\b", text))

    kg_price = bool(re.search(rf"\bpr\.?\s*kg\s*{number}\b", text))
    l_price = bool(re.search(rf"\bpr\.?\s*(?:l|liter)\s*{number}\b", text))
    piece_price = bool(re.search(rf"\bpr\.?\s*stk\.?\s*{number}\b", text))

    return (exact_kg and kg_price) or (exact_l and l_price) or (exact_piece and piece_price)


def _available(candidates, kind: str):
    return [
        candidate
        for candidate in candidates
        if not _is_quarantined(kind, candidate.publication, candidate)
    ]


async def _publish_pending_once(publications) -> dict:
    """Release one valid source publication before doing any paid enrichment."""
    if readiness_store_version() < READINESS_STORE_VERSION:
        return {
            "status": "readiness-migration-pending",
            "readiness": readiness_status_payload(),
        }

    pending = pending_publication_records()
    if not pending:
        return {"status": "no-publication-pending"}

    record = pending[0]
    publication_id = str(record.get("publication_id") or "")
    expected = str(record.get("fingerprint") or "")
    publication = next(
        (
            item
            for item in publications
            if str(item.id) == publication_id and item.status != "expired"
        ),
        None,
    )
    if publication is None:
        mark_failed(publication_id, expected, "publication-not-found")
        return {"status": "publication-not-found", "publication_id": publication_id}

    actual = publication_fingerprint(publication)
    if actual != expected:
        mark_failed(publication_id, expected, "publication-version-changed")
        return {"status": "publication-version-changed", "publication_id": publication_id}

    if not mark_ready(publication):
        return {"status": "publication-version-changed", "publication_id": publication_id}

    removed = _clear_legacy_publication_stall(publication_id)
    return {
        "status": "published",
        "publication_id": publication_id,
        "retailer": publication.retailer,
        "title": publication.title,
        "legacy_stalls_removed": removed,
        "readiness": readiness_status_payload(),
    }


async def _background_enrichment_once(publications) -> dict:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "enrichment-disabled"}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "enrichment-missing-api-key"}

    total_limit = max(1, int(os.getenv("LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE", "8")))
    page_limit = max(0, int(os.getenv("LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE", "4")))
    pricing_limit = max(0, int(os.getenv("LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE", "3")))
    fallback_limit = max(0, int(os.getenv("LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE", "1")))
    variant_limit = max(0, int(os.getenv("LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE", "0")))

    processed_pages = 0
    processed_pricing = 0
    processed_fallback = 0
    processed_variants = 0
    quarantined_now = 0
    deterministic_skips = 0
    stop_status = None

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        page_candidates = _available(collect_page_audit_candidates(publications), "page")
        for candidate in page_candidates[: min(page_limit, total_limit)]:
            result = await analyze_page_audit(candidate, client=client)
            status = str(result.get("status") or "")
            if status in _STOP_STATUSES:
                stop_status = status
                break
            if status != "completed":
                _quarantine(
                    "page",
                    candidate.publication,
                    candidate,
                    str(result.get("error") or status or "page-audit-unresolved"),
                )
                quarantined_now += 1
                continue
            processed_pages += 1

        remaining = max(0, total_limit - processed_pages)
        if stop_status is None and remaining and pricing_limit:
            pricing_candidates, _ = _split_crop_candidates(collect_crop_candidates(publications))
            pricing_candidates = _available(pricing_candidates, "pricing")

            payable = []
            for candidate in pricing_candidates:
                if _provider_unit_equivalence(candidate):
                    _quarantine(
                        "pricing",
                        candidate.publication,
                        candidate,
                        "deterministic-provider-unit-equivalence",
                    )
                    deterministic_skips += 1
                    continue
                payable.append(candidate)

            for candidate in payable[: min(pricing_limit, remaining)]:
                before_store = load_store()
                previous_semantic = before_store.get("semantic_facts", {}).get(
                    offer_key(candidate.offer)
                )
                result = await analyze_crop_candidate(candidate, client=client)
                status = str(result.get("status") or "")
                if status in _STOP_STATUSES:
                    stop_status = status
                    break
                if not _mandatory_pricing_crop_verified(result, config, candidate.offer):
                    error = str(result.get("error") or status or "pricing-crop-unresolved")
                    _requeue_mandatory_crop(candidate, previous_semantic, error)
                    _quarantine("pricing", candidate.publication, candidate, error)
                    quarantined_now += 1
                    continue
                processed_pricing += 1

        remaining = max(0, total_limit - processed_pages - processed_pricing)
        if stop_status is None and remaining and fallback_limit:
            fallback_candidates = _available(
                _paid_candidates(collect_candidates(publications)),
                "fallback",
            )
            for candidate in fallback_candidates[: min(fallback_limit, remaining)]:
                result = await analyze_candidate(candidate, client=client)
                status = str(result.get("status") or "")
                if status in _STOP_STATUSES:
                    stop_status = status
                    break
                if status not in {"completed", "no-change"}:
                    _quarantine(
                        "fallback",
                        candidate.publication,
                        candidate,
                        str(result.get("error") or status or "fallback-unresolved"),
                    )
                    quarantined_now += 1
                    continue
                processed_fallback += 1

        remaining = max(
            0,
            total_limit - processed_pages - processed_pricing - processed_fallback,
        )
        if (
            stop_status is None
            and remaining
            and variant_limit
            and _cost_policy.variant_crop_budget_allows(config)
        ):
            _, variant_candidates = _split_crop_candidates(collect_crop_candidates(publications))
            variant_candidates = _available(variant_candidates, "variant")
            for candidate in variant_candidates[: min(variant_limit, remaining)]:
                if not _cost_policy.variant_crop_budget_allows(config):
                    break
                result = await analyze_crop_candidate(candidate, client=client)
                status = str(result.get("status") or "")
                if status in _STOP_STATUSES:
                    stop_status = status
                    break
                if status not in {"completed", "no-change"}:
                    _quarantine(
                        "variant",
                        candidate.publication,
                        candidate,
                        str(result.get("error") or status or "variant-unresolved"),
                    )
                    quarantined_now += 1
                    continue
                processed_variants += 1

    quarantine_count = len(_load_quarantine())
    processed = processed_pages + processed_pricing + processed_fallback + processed_variants
    return {
        "status": stop_status or ("enrichment-progress" if (processed or quarantined_now or deterministic_skips) else "enrichment-idle"),
        "processed": processed,
        "pages_processed": processed_pages,
        "pricing_crops_processed": processed_pricing,
        "fallback_processed": processed_fallback,
        "variant_crops_processed": processed_variants,
        "quarantined_now": quarantined_now,
        "deterministic_skips": deterministic_skips,
        "quarantine_count": quarantine_count,
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
                "readiness": readiness_status_payload(),
            }

        publications = await fetch_all_publications()
        release = await _publish_pending_once(publications)
        if release.get("status") == "published":
            # The source flyer is now customer-usable. Enrichment is deliberately
            # a separate phase so a Luna/API/budget failure can never roll it back.
            return release

        enrichment = await _background_enrichment_once(publications)
        return {
            **enrichment,
            "publication_release": release,
        }


async def main() -> None:
    idle_seconds = max(10, int(os.getenv("LUNA_QUEUE_POLL_SECONDS", "15")))
    progress_seconds = max(1, int(os.getenv("LUNA_RESILIENT_PROGRESS_SECONDS", "3")))
    pause_seconds = max(300, int(os.getenv("LUNA_QUEUE_ERROR_BACKOFF_SECONDS", "120")))

    while True:
        try:
            result = await run_once()
            log.info("Luna resilient event: %s", result)
            status = str(result.get("status") or "")
            if status == "published":
                await asyncio.sleep(1)
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
        except Exception:
            log.exception("Luna resilient worker failed")
            await asyncio.sleep(pause_seconds)


if __name__ == "__main__":
    asyncio.run(main())

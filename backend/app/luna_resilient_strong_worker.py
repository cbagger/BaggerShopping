from __future__ import annotations

"""Strong, resilient and coverage-first Luna worker.

Source flyers remain non-blocking: provider data can be served immediately.
What changes here is the quality pipeline around that release. Every current
flyer gets an explicit full-page member-price coverage state, and unfinished
coverage always has priority over optional enrichment/backfill. A new-flyer push
can therefore wait for quality without making the flyer itself unavailable.
"""

import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import luna_member_coverage as coverage
from . import luna_resilient_worker as base
from .flyer_readiness import publication_fingerprint, publication_is_ready
from .luna_enrichment import load_config, save_config


# Preserve Luna's established analysis strength. Coverage-first processing only
# changes ordering and failure isolation; it does not weaken prompts, guards,
# model choice or the number of high-quality page/pricing analyses available.
os.environ.setdefault("LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE", "20")
os.environ.setdefault("LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE", "10")
os.environ.setdefault("LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE", "10")
os.environ.setdefault("LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE", "20")
os.environ.setdefault("LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE", "5")

DEFAULT_FAILURE_ATTEMPTS = 2
BUDGET_POLICY_VERSION = 1
TARGET_MONTHLY_BUDGET_DKK = 100.0


def _retry_path() -> Path:
    return Path(os.getenv("LUNA_RETRY_STATE_PATH", "/data/luna-retry-work.json"))


def _load_retry_state() -> dict[str, dict]:
    path = _retry_path()
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


def _save_retry_state(rows: dict[str, dict]) -> None:
    path = _retry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(rows) > 4000:
        rows = dict(list(rows.items())[-4000:])
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "contract": base.RESILIENCE_CONTRACT_VERSION,
                "items": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "utf-8",
    )
    temporary.replace(path)


def _max_failure_attempts() -> int:
    try:
        return max(
            1,
            int(os.getenv("LUNA_RESILIENT_FAILURE_ATTEMPTS", str(DEFAULT_FAILURE_ATTEMPTS))),
        )
    except (TypeError, ValueError):
        return DEFAULT_FAILURE_ATTEMPTS


_original_quarantine = base._quarantine


def _bounded_quarantine(kind: str, publication, candidate, reason: str) -> None:
    """Retry unresolved AI work once, then isolate only that exact candidate."""
    if str(reason) == "deterministic-provider-unit-equivalence":
        _original_quarantine(kind, publication, candidate, reason)
        return

    key = base._quarantine_key(kind, publication, candidate)
    rows = _load_retry_state()
    row = dict(rows.get(key) or {})
    attempts = int(row.get("attempts") or 0) + 1
    rows[key] = {
        "contract": base.RESILIENCE_CONTRACT_VERSION,
        "kind": str(kind),
        "publication_id": str(publication.id),
        "publication_fingerprint": base._source_fingerprint(publication),
        "candidate_fingerprint": base._candidate_fingerprint(candidate),
        "attempts": attempts,
        "last_reason": str(reason)[:500],
    }
    _save_retry_state(rows)

    if attempts >= _max_failure_attempts():
        _original_quarantine(kind, publication, candidate, reason)


def install_strength_policy() -> None:
    base._quarantine = _bounded_quarantine


def ensure_budget_policy() -> dict:
    """Apply the explicitly approved 100 DKK cap once, then respect later edits."""
    config = load_config()
    if int(config.get("kurv_budget_policy_version") or 0) >= BUDGET_POLICY_VERSION:
        return config
    return save_config(
        {
            "monthly_budget_dkk": TARGET_MONTHLY_BUDGET_DKK,
            "recommended_monthly_budget_dkk": TARGET_MONTHLY_BUDGET_DKK,
            "kurv_budget_policy_version": BUDGET_POLICY_VERSION,
        }
    )


def _member_fallback_candidates(publication) -> list:
    candidates = base._available(
        base._paid_candidates(base.collect_candidates([publication])),
        "fallback",
    )
    result = []
    for candidate in candidates:
        reasons = {
            str(value).casefold()
            for value in (getattr(candidate.decision, "reasons", ()) or ())
            if str(value)
        }
        if any("member" in reason or "membership" in reason for reason in reasons):
            result.append(candidate)
    return result


def _hard_quarantine_count(publication) -> int:
    publication_id = str(publication.id)
    source_fingerprint = base._source_fingerprint(publication)
    count = 0
    for row in base._load_quarantine().values():
        if not isinstance(row, dict):
            continue
        if str(row.get("publication_id") or "") != publication_id:
            continue
        if str(row.get("publication_fingerprint") or "") != source_fingerprint:
            continue
        if str(row.get("kind") or "") not in {"page", "pricing", "fallback"}:
            continue
        if str(row.get("reason") or "") == "deterministic-provider-unit-equivalence":
            continue
        count += 1
    return count


def _coverage_snapshot(publication) -> dict:
    page_candidates = base._available(
        base.collect_page_audit_candidates([publication]),
        "page",
    )
    pricing_candidates, _ = base._split_crop_candidates(
        base.collect_crop_candidates([publication])
    )
    pricing_candidates = base._available(pricing_candidates, "pricing")
    member_fallback = _member_fallback_candidates(publication)
    return coverage.update_snapshot(
        publication_id=publication.id,
        fingerprint=publication_fingerprint(publication),
        retailer=publication.retailer,
        title=publication.title,
        valid_from=getattr(publication, "valid_from", None),
        pages_remaining=len(page_candidates),
        pricing_remaining=len(pricing_candidates),
        member_fallback_remaining=len(member_fallback),
        hard_quarantined=_hard_quarantine_count(publication),
    )


def _sync_coverage(publications) -> None:
    for publication in publications:
        if publication.status == "expired" or not publication_is_ready(publication):
            continue
        fingerprint = publication_fingerprint(publication)
        coverage.ensure_pending(
            publication_id=publication.id,
            fingerprint=fingerprint,
            retailer=publication.retailer,
            title=publication.title,
            valid_from=getattr(publication, "valid_from", None),
        )
        _coverage_snapshot(publication)


def _date_sort_value(value: object) -> int:
    raw = str(value or "").strip()
    try:
        return int(datetime.strptime(raw, "%d.%m.%Y").strftime("%Y%m%d"))
    except ValueError:
        return 0


def _coverage_focus(publications):
    pending = []
    for publication in publications:
        if publication.status == "expired" or not publication_is_ready(publication):
            continue
        row = coverage.get(publication.id, publication_fingerprint(publication))
        if row and row.get("status") == "pending":
            pending.append(publication)
    if not pending:
        return None
    pending.sort(
        key=lambda publication: (
            -_date_sort_value(getattr(publication, "valid_from", None)),
            publication.retailer.casefold(),
            publication.id,
        )
    )
    return pending[0]


@contextmanager
def _temporary_limits(**values: int):
    previous: dict[str, str | None] = {}
    try:
        for name, value in values.items():
            previous[name] = os.environ.get(name)
            os.environ[name] = str(value)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def run_once() -> dict:
    install_strength_policy()
    ensure_budget_policy()

    with base._execution_lease() as acquired:
        if not acquired:
            return {"status": "busy", "coverage": coverage.status_payload()}

        publications = await base.fetch_all_publications()
        release = await base._publish_pending_once(publications)
        if release.get("status") == "published":
            publication_id = str(release.get("publication_id") or "")
            publication = next(
                (item for item in publications if str(item.id) == publication_id),
                None,
            )
            if publication is not None:
                coverage.ensure_pending(
                    publication_id=publication.id,
                    fingerprint=publication_fingerprint(publication),
                    retailer=publication.retailer,
                    title=publication.title,
                    valid_from=getattr(publication, "valid_from", None),
                )
            return {**release, "coverage": coverage.status_payload()}

        _sync_coverage(publications)
        focus = _coverage_focus(publications)

        if focus is not None:
            # Finish page/member-price coverage before spending on optional
            # variants. The full-strength variant pipeline resumes afterwards.
            with _temporary_limits(
                LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE=20,
                LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE=10,
                LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE=10,
                LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE=4,
                LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE=0,
            ):
                enrichment = await base._background_enrichment_once([focus])
            snapshot = _coverage_snapshot(focus)
            return {
                **enrichment,
                "coverage_focus": {
                    "publication_id": focus.id,
                    "retailer": focus.retailer,
                    "title": focus.title,
                    **snapshot,
                },
                "coverage": coverage.status_payload(),
                "publication_release": release,
            }

        # Once every current flyer has full mandatory coverage, continue optional
        # enrichment at a deliberately gentle rate. This keeps quality improving
        # without allowing old variant/backfill work to race through the 100 DKK
        # safety cap merely because the worker is idle.
        with _temporary_limits(
            LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE=4,
            LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE=0,
            LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE=0,
            LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE=1,
            LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE=2,
        ):
            enrichment = await base._background_enrichment_once(publications)
        return {
            **enrichment,
            "coverage": coverage.status_payload(),
            "publication_release": release,
        }


async def main() -> None:
    install_strength_policy()
    ensure_budget_policy()

    idle_seconds = max(10, int(os.getenv("LUNA_QUEUE_POLL_SECONDS", "15")))
    progress_seconds = max(1, int(os.getenv("LUNA_RESILIENT_PROGRESS_SECONDS", "3")))
    pause_seconds = max(300, int(os.getenv("LUNA_QUEUE_ERROR_BACKOFF_SECONDS", "120")))

    while True:
        try:
            result = await run_once()
            base.log.info("Luna resilient strong event: %s", result)
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
        except Exception:
            base.log.exception("Luna resilient strong worker failed")
            await asyncio.sleep(pause_seconds)


if __name__ == "__main__":
    asyncio.run(main())

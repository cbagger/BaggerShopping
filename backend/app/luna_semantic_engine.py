from __future__ import annotations

"""First-class composition of Kurv's semantic audit safety/cost policies.

Historically the worker called ``install()`` functions that replaced globals in
``luna_semantic_audit`` before importing them. This engine invokes the same
validated policies explicitly, so module import order can no longer change the
pricing/variant safety contract.
"""

import json
import os
import time
from typing import Any, Iterable

import httpx

from . import luna_cost_policy as cost_policy
from . import luna_semantic_audit as base
from . import luna_semantic_guards as guards
from .luna_enrichment import (
    _output_text,
    budget_allows_request,
    load_config,
    load_store,
    offer_fingerprint,
    offer_pricing_signature,
    save_store,
)
from .meny_flyer import Offer, Publication


PageAuditCandidate = base.PageAuditCandidate
CropCandidate = base.CropCandidate
offer_key = base.offer_key
semantic_status_payload = base.semantic_status_payload


def page_fingerprint(
    publication: Publication,
    page_number: int,
    offers: Iterable[Offer],
) -> str:
    return guards._versioned_page_fingerprint(publication, page_number, offers)


def collect_page_audit_candidates(
    publications: Iterable[Publication],
) -> list[PageAuditCandidate]:
    store = load_store()
    audits = store.get("page_audits", {})
    max_failures = max(1, int(load_config().get("page_audit_max_failures", 2)))
    result: list[PageAuditCandidate] = []

    for publication in publications:
        if publication.status == "expired":
            continue
        by_page: dict[int, list[Offer]] = {}
        for offer in publication.structured_offers:
            page = offer.page_number
            if page is None or page <= 0 or page > len(publication.page_image_urls):
                continue
            by_page.setdefault(page, []).append(offer)

        for page_number, offers in by_page.items():
            image_url = publication.page_image_urls[page_number - 1]
            if not image_url:
                continue
            fingerprint = page_fingerprint(publication, page_number, offers)
            existing = audits.get(fingerprint)
            if isinstance(existing, dict):
                status = existing.get("status")
                if status in {"completed", "pending"}:
                    continue
                if status == "failed" and int(existing.get("attempts") or 0) >= max_failures:
                    continue
            result.append(PageAuditCandidate(
                fingerprint=fingerprint,
                publication=publication,
                page_number=page_number,
                image_url=image_url,
                offers=tuple(offers),
            ))

    return sorted(
        result,
        key=lambda item: (
            item.publication.retailer.casefold(),
            item.publication.id,
            item.page_number,
        ),
    )


def _page_schema(candidate: PageAuditCandidate) -> dict[str, Any]:
    ids = [offer.id for offer in candidate.offers]
    count = max(1, len(ids))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "offers": {
                "type": "array",
                "items": guards._strict_fact_schema(
                    include_offer_id=True,
                    offer_ids=ids,
                ),
                "minItems": count,
                "maxItems": count,
            }
        },
        "required": ["offers"],
    }


def _page_request_body(
    candidate: PageAuditCandidate,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [
            {"role": "developer", "content": [{
                "type": "input_text",
                "text": guards._strict_page_instructions(),
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }]},
            {"role": "user", "content": [
                {"type": "input_text", "text": json.dumps(
                    base._page_context(candidate),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )},
                {"type": "input_image", "image_url": candidate.image_url, "detail": "high"},
            ]},
        ],
        "prompt_cache_key": "kurv-page-audit-member-price-sanity-v2",
        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_page_audit",
                "strict": True,
                "schema": _page_schema(candidate),
            },
        },
        "max_output_tokens": int(config.get("page_audit_max_output_tokens", 4000)),
    }


async def analyze_page_audit(
    candidate: PageAuditCandidate,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "disabled"}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing-api-key"}

    store = load_store()
    if not budget_allows_request(config, store):
        return {"status": "budget-exhausted"}

    previous = store.get("page_audits", {}).get(candidate.fingerprint, {})
    attempts = int(previous.get("attempts") or 0) + 1 if isinstance(previous, dict) else 1
    record = {
        "status": "pending",
        "retailer": candidate.publication.retailer,
        "publication_id": candidate.publication.id,
        "page_number": candidate.page_number,
        "offer_count": len(candidate.offers),
        "attempts": attempts,
        "created_at": int(time.time()),
    }
    store.setdefault("page_audits", {})[candidate.fingerprint] = record
    save_store(store)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)

    try:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=_page_request_body(candidate, config),
        )
        response.raise_for_status()
        body = response.json()
        text = _output_text(body)
        parsed = json.loads(text) if text else None
        facts_list = guards._strict_validate_page_output(
            parsed,
            {offer.id for offer in candidate.offers},
        )

        store = load_store()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        base._record_usage(store, usage, config, kind="page-audit")
        row = store.setdefault("page_audits", {}).setdefault(candidate.fingerprint, record)

        if facts_list is None:
            row.update({
                "status": "failed",
                "error": "invalid-structured-output",
                "updated_at": int(time.time()),
            })
            save_store(store)
            return dict(row)

        threshold = float(config.get("min_apply_confidence", 0.96))
        by_id = {offer.id: offer for offer in candidate.offers}
        crop_needed = 0

        for facts in facts_list:
            offer = by_id[facts["offer_id"]]
            needs_crop = cost_policy._balanced_server_needs_crop(
                offer,
                facts,
                threshold,
            )
            reasons = cost_policy._balanced_crop_reasons(
                offer,
                facts,
                needs_crop,
            )
            if needs_crop:
                crop_needed += 1

            current = store.setdefault("semantic_facts", {}).get(offer_key(offer))
            if not isinstance(current, dict) or current.get("source") != "crop":
                store["semantic_facts"][offer_key(offer)] = {
                    "source": "page-audit",
                    "page_fingerprint": candidate.fingerprint,
                    "retailer": offer.retailer,
                    "publication_id": offer.publication_id,
                    "offer_id": offer.id,
                    "facts": facts,
                    "needs_crop": needs_crop,
                    "crop_reasons": reasons,
                    "updated_at": int(time.time()),
                }
            guards._index_page_pricing_upgrading_legacy(
                store,
                offer,
                facts,
                needs_crop=needs_crop,
                page_fingerprint_value=candidate.fingerprint,
            )

        row.update({
            "status": "completed",
            "model": body.get("model") or config.get("model"),
            "response_id": body.get("id"),
            "usage": usage,
            "audited_offers": len(facts_list),
            "crop_needed": crop_needed,
            "updated_at": int(time.time()),
        })
        store.setdefault("events", []).append({
            "at": int(time.time()),
            "event": "page-audit",
            "page_fingerprint": candidate.fingerprint,
            "status": "completed",
            "retailer": candidate.publication.retailer,
            "page_number": candidate.page_number,
            "offer_count": len(candidate.offers),
            "crop_needed": crop_needed,
        })
        save_store(store)
        return dict(row)
    except Exception as exc:
        store = load_store()
        row = store.setdefault("page_audits", {}).setdefault(candidate.fingerprint, record)
        row.update({
            "status": "failed",
            "error": str(exc)[:500],
            "updated_at": int(time.time()),
        })
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


def collect_crop_candidates(
    publications: Iterable[Publication],
) -> list[CropCandidate]:
    return guards._crop_candidates_allowing_build58_reverification(publications)


def _crop_schema() -> dict[str, Any]:
    return guards._strict_fact_schema(include_offer_id=False)


def _crop_request_body(
    candidate: CropCandidate,
    config: dict[str, Any],
) -> dict[str, Any]:
    image_url, detail = base._crop_image(candidate)
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": json.dumps(
            base._crop_context(candidate),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }]
    if image_url:
        content.append({"type": "input_image", "image_url": image_url, "detail": detail})
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [
            {"role": "developer", "content": [{
                "type": "input_text",
                "text": guards._strict_crop_instructions(),
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }]},
            {"role": "user", "content": content},
        ],
        "prompt_cache_key": "kurv-crop-member-price-sanity-v2",
        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_crop_facts",
                "strict": True,
                "schema": _crop_schema(),
            },
        },
        "max_output_tokens": int(config.get("crop_max_output_tokens", 1200)),
    }


async def analyze_crop_candidate(
    candidate: CropCandidate,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "disabled"}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing-api-key"}

    store = load_store()
    if not budget_allows_request(config, store):
        return {"status": "budget-exhausted"}

    signature = offer_pricing_signature(candidate.offer)
    record = {
        "status": "pending",
        "analysis_level": "crop",
        "retailer": candidate.offer.retailer,
        "publication_id": candidate.offer.publication_id,
        "offer_id": candidate.offer.id,
        "product_name": candidate.offer.product_name,
        "pricing_signature": signature,
        "page_fingerprint": candidate.page_fingerprint,
        "reasons": list(candidate.reasons),
        "created_at": int(time.time()),
    }
    store.setdefault("records", {})[candidate.fingerprint] = record
    save_store(store)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)

    try:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=_crop_request_body(candidate, config),
        )
        response.raise_for_status()
        body = response.json()
        text = _output_text(body)
        parsed = json.loads(text) if text else None
        facts = base._validate_fact_row(parsed)

        store = load_store()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        base._record_usage(store, usage, config, kind="crop")
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)

        if facts is None:
            row.update({
                "status": "failed",
                "error": "invalid-structured-output",
                "updated_at": int(time.time()),
            })
            save_store(store)
            return dict(row)

        facts["membership_price_visible"] = bool(facts.get("membership_price_visible"))
        facts["same_offer"] = bool(facts.get("visible"))
        threshold = float(config.get("min_apply_confidence", 0.96))
        useful = bool(facts.get("visible")) and (
            float(facts.get("pricing_confidence") or 0) >= threshold
            or float(facts.get("variant_confidence") or 0) >= threshold
            or float(facts.get("identity_confidence") or 0) >= threshold
        )
        status = "completed" if useful else "no-change"
        row.update({
            "status": status,
            "facts": base._pricing_record_facts(facts),
            "semantic_facts": facts,
            "model": body.get("model") or config.get("model"),
            "response_id": body.get("id"),
            "usage": usage,
            "updated_at": int(time.time()),
        })
        store.setdefault("pricing_index", {})[signature] = candidate.fingerprint

        if status == "completed":
            store.setdefault("semantic_facts", {})[offer_key(candidate.offer)] = {
                "source": "crop",
                "page_fingerprint": candidate.page_fingerprint,
                "retailer": candidate.offer.retailer,
                "publication_id": candidate.offer.publication_id,
                "offer_id": candidate.offer.id,
                "facts": facts,
                "needs_crop": False,
                "crop_reasons": [],
                "updated_at": int(time.time()),
            }

        store.setdefault("events", []).append({
            "at": int(time.time()),
            "event": "crop-analysis",
            "fingerprint": candidate.fingerprint,
            "status": status,
            "retailer": candidate.offer.retailer,
            "page_number": candidate.offer.page_number,
        })
        save_store(store)
        return dict(row)
    except Exception as exc:
        store = load_store()
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)
        row.update({
            "status": "failed",
            "error": str(exc)[:500],
            "updated_at": int(time.time()),
        })
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


__all__ = [
    "CropCandidate",
    "PageAuditCandidate",
    "analyze_crop_candidate",
    "analyze_page_audit",
    "collect_crop_candidates",
    "collect_page_audit_candidates",
    "offer_key",
    "page_fingerprint",
    "semantic_status_payload",
]

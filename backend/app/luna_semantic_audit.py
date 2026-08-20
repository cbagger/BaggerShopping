from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

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


_SIZE_ONLY_VARIANT_RE = re.compile(
    r"^\s*(?:ca\.?\s*)?(?:"
    r"\d+(?:[.,]\d+)?\s*(?:[-–]\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:g|kg|ml|cl|dl|l|stk\.?|styk(?:ker)?|pk\.?|pakker?)"
    r"|\d+\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:g|kg|ml|cl|dl|l|stk\.?)"
    r")\s*$",
    re.IGNORECASE,
)
_GENERIC_VARIANT_RE = re.compile(
    r"^\s*(?:flere\s+varianter|frit\s+valg|assorterede?|diverse|flere\s+slags)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PageAuditCandidate:
    fingerprint: str
    publication: Publication
    page_number: int
    image_url: str
    offers: tuple[Offer, ...]


@dataclass(frozen=True)
class CropCandidate:
    fingerprint: str
    publication: Publication
    offer: Offer
    page_fingerprint: str
    reasons: tuple[str, ...]


def offer_key(offer: Offer) -> str:
    raw = json.dumps(
        {
            "publication": offer.publication_id,
            "offer": offer.id,
            "retailer": offer.retailer,
            "page": offer.page_number,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _stable_image_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def page_fingerprint(publication: Publication, page_number: int, offers: Iterable[Offer]) -> str:
    image_url = (
        publication.page_image_urls[page_number - 1]
        if 0 < page_number <= len(publication.page_image_urls)
        else ""
    )
    payload = {
        "publication": publication.id,
        "retailer": publication.retailer,
        "page": page_number,
        "image": _stable_image_url(image_url),
        "text": publication.page_texts[page_number - 1][:1200]
        if 0 < page_number <= len(publication.page_texts)
        else "",
        "offers": [
            {
                "id": offer.id,
                "name": offer.product_name,
                "price": offer.price,
                "normal_price": offer.normal_price,
                "box": [
                    offer.hotspot_x,
                    offer.hotspot_y,
                    offer.hotspot_width,
                    offer.hotspot_height,
                ],
                "variants": [variant.name for variant in offer.variants],
            }
            for offer in offers
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def collect_page_audit_candidates(publications: Iterable[Publication]) -> list[PageAuditCandidate]:
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
            result.append(
                PageAuditCandidate(
                    fingerprint=fingerprint,
                    publication=publication,
                    page_number=page_number,
                    image_url=image_url,
                    offers=tuple(offers),
                )
            )
    return sorted(
        result,
        key=lambda item: (
            item.publication.retailer.casefold(),
            item.publication.id,
            item.page_number,
        ),
    )


def _box(offer: Offer) -> dict[str, float] | None:
    values = (
        offer.hotspot_x,
        offer.hotspot_y,
        offer.hotspot_width,
        offer.hotspot_height,
    )
    if any(value is None for value in values):
        return None
    return {
        "x": float(offer.hotspot_x),
        "y": float(offer.hotspot_y),
        "width": float(offer.hotspot_width),
        "height": float(offer.hotspot_height),
    }


def _fact_schema(*, include_offer_id: bool, offer_ids: list[str] | None = None) -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    nullable_string = {"type": ["string", "null"]}
    properties: dict[str, Any] = {
        "visible": {"type": "boolean"},
        "product_name": nullable_string,
        "brand": nullable_string,
        "ordinary_price": nullable_number,
        "member_price": nullable_number,
        "member_program": nullable_string,
        "member_app": nullable_string,
        "requires_activation": {"type": "boolean"},
        "before_price": nullable_number,
        "unit_price": nullable_string,
        "package_size": nullable_string,
        "multiple_products": {"type": "boolean"},
        "variants": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "identity_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "pricing_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "variant_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_crop_verification": {"type": "boolean"},
    }
    required = list(properties)
    if include_offer_id:
        properties = {
            "offer_id": {"type": "string", "enum": offer_ids or []},
            **properties,
        }
        required = ["offer_id", *required]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _page_schema(candidate: PageAuditCandidate) -> dict[str, Any]:
    ids = [offer.id for offer in candidate.offers]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "offers": {
                "type": "array",
                "items": _fact_schema(include_offer_id=True, offer_ids=ids),
                "maxItems": max(1, len(ids)),
            }
        },
        "required": ["offers"],
    }


def _page_context(candidate: PageAuditCandidate) -> dict[str, Any]:
    targets = []
    for offer in candidate.offers:
        targets.append(
            {
                "offer_id": offer.id,
                "provider_name": offer.product_name,
                "provider_price": offer.price,
                "provider_reference_price": offer.normal_price,
                "provider_unit_price": offer.unit_price,
                "provider_variants": [variant.name for variant in offer.variants][:12],
                "provider_variant_confidence": offer.variant_confidence,
                "provider_text": offer.raw_text[:700],
                "hotspot_normalized": _box(offer),
            }
        )

    return {
        "retailer": candidate.publication.retailer,
        "publication": candidate.publication.title,
        "page": candidate.page_number,
        "targets": targets,
    }


def _page_instructions() -> str:
    return (
        "You are Kurv's semantic flyer page auditor. Inspect the full flyer page once and audit "
        "ONLY the target offer hotspots listed below. Return one result for each target you can "
        "associate with the visual advert, using the exact offer_id. Never borrow a price, badge, "
        "variant or legal condition from a neighbouring advert. ordinary_price means the amount "
        "a non-member pays for that exact campaign. member_price is only a price explicitly tied "
        "to membership/app/club/plus for that exact campaign. A kg/l/100g/100ml price, deposit, "
        "membership fee, package count or old before-price is never ordinary_price/member_price. "
        "package_size is metadata such as 500 g or 4 x 25 cl and must NOT be returned as a variant. "
        "variants must contain only concrete named choices visible for the same campaign; never "
        "return generic text such as 'flere varianter', 'frit valg', weights or pack sizes. "
        "multiple_products is true whenever the advert visibly covers more than one concrete "
        "product/variant, even if you cannot read every name. requires_activation is true only "
        "when the advert explicitly requires activation/clipping/coupon action; merely needing "
        "membership/app is false. Set needs_crop_verification when text is too small, the hotspot "
        "overlaps neighbours, provider facts conflict with the image, or any important price/"
        "variant fact is not safe from the full-page view. If unsure, use null/empty values and "
        "lower confidence rather than guessing."
    )


def _page_prompt(candidate: PageAuditCandidate) -> str:
    return _page_instructions() + "\n\n" + json.dumps(
        _page_context(candidate), ensure_ascii=False, separators=(",", ":")
    )


def _page_request_body(candidate: PageAuditCandidate, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [
            {"role": "developer", "content": [{
                "type": "input_text",
                "text": _page_instructions(),
            }]},
            {"role": "user", "content": [
                {"type": "input_text", "text": json.dumps(
                    _page_context(candidate), ensure_ascii=False, separators=(",", ":")
                )},
                {"type": "input_image", "image_url": candidate.image_url, "detail": "high"},
            ]},
        ],
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


def _safe_variant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name:
        return None
    if _SIZE_ONLY_VARIANT_RE.fullmatch(name) or _GENERIC_VARIANT_RE.fullmatch(name):
        return None
    return name


def _validate_fact_row(row: object, *, require_offer_id: bool = False) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    required = {
        "visible",
        "product_name",
        "brand",
        "ordinary_price",
        "member_price",
        "member_program",
        "member_app",
        "requires_activation",
        "before_price",
        "unit_price",
        "package_size",
        "multiple_products",
        "variants",
        "identity_confidence",
        "pricing_confidence",
        "variant_confidence",
        "needs_crop_verification",
    }
    if require_offer_id:
        required.add("offer_id")
    if not required.issubset(row):
        return None
    for key in ("identity_confidence", "pricing_confidence", "variant_confidence"):
        score = row.get(key)
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            return None
    variants = row.get("variants")
    if not isinstance(variants, list) or not all(isinstance(item, str) for item in variants):
        return None
    result = dict(row)
    result["variants"] = list(
        dict.fromkeys(
            name
            for item in variants
            if (name := _safe_variant(item)) is not None
        )
    )[:12]
    return result


def _validate_page_output(value: object, allowed_ids: set[str]) -> list[dict[str, Any]] | None:
    if not isinstance(value, dict) or not isinstance(value.get("offers"), list):
        return None
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value["offers"]:
        row = _validate_fact_row(raw, require_offer_id=True)
        if row is None:
            return None
        offer_id = row.get("offer_id")
        if not isinstance(offer_id, str) or offer_id not in allowed_ids or offer_id in seen:
            return None
        seen.add(offer_id)
        result.append(row)
    return result


def _price_relation_valid(facts: dict[str, Any]) -> bool:
    member = facts.get("member_price")
    ordinary = facts.get("ordinary_price")
    if member is not None and (not isinstance(member, (int, float)) or member <= 0):
        return False
    if ordinary is not None and (not isinstance(ordinary, (int, float)) or ordinary <= 0):
        return False
    if member is not None and ordinary is not None and float(member) >= float(ordinary):
        return False
    return True


def _server_needs_crop(offer: Offer, facts: dict[str, Any], threshold: float) -> bool:
    if not facts.get("visible"):
        return True
    if facts.get("needs_crop_verification"):
        return True
    if not _price_relation_valid(facts):
        return True

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    variant_conf = float(facts.get("variant_confidence") or 0)

    if facts.get("member_price") is not None and pricing_conf < threshold:
        return True
    if facts.get("multiple_products") and (
        variant_conf < 0.99 or len(facts.get("variants") or []) < 2
    ):
        return True

    visual_prices = {
        float(value)
        for value in (facts.get("ordinary_price"), facts.get("member_price"))
        if isinstance(value, (int, float))
    }
    if (
        offer.price is not None
        and pricing_conf >= threshold
        and visual_prices
        and all(abs(float(offer.price) - value) > 0.005 for value in visual_prices)
    ):
        return True

    return False


def _usage_cost_dkk(usage: dict[str, Any], config: dict[str, Any]) -> float:
    from .luna_enrichment import _usage_cost_dkk as shared_usage_cost_dkk

    return shared_usage_cost_dkk(usage, config)


def _record_usage(store: dict[str, Any], usage: dict[str, Any], config: dict[str, Any], *, kind: str) -> None:
    from .luna_enrichment import _add_usage_to_row, month_key

    row = store.setdefault("usage", {}).setdefault(month_key(), {})
    _add_usage_to_row(row, usage, config)

    kind_row = row.setdefault("by_kind", {}).setdefault(kind, {})
    _add_usage_to_row(kind_row, usage, config, timestamp=False)


def _crop_reasons(offer: Offer, facts: dict[str, Any], needs_crop: bool) -> list[str]:
    result: list[str] = []
    if facts.get("needs_crop_verification"):
        result.append("page-audit-model-requested-crop")
    if not _price_relation_valid(facts):
        result.append("page-audit-price-role-conflict")
    if facts.get("multiple_products") and (
        float(facts.get("variant_confidence") or 0) < 0.99
        or len(facts.get("variants") or []) < 2
    ):
        result.append("page-audit-variant-uncertain")
    visual_prices = {
        float(value)
        for value in (facts.get("ordinary_price"), facts.get("member_price"))
        if isinstance(value, (int, float))
    }
    if (
        offer.price is not None
        and float(facts.get("pricing_confidence") or 0) >= float(load_config().get("min_apply_confidence", 0.96))
        and visual_prices
        and all(abs(float(offer.price) - value) > 0.005 for value in visual_prices)
    ):
        result.append("page-audit-provider-price-conflict")
    if needs_crop and not result:
        result.append("page-audit-needs-crop")
    return result


def _pricing_record_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "same_offer": bool(facts.get("visible")),
        "product_name": facts.get("product_name"),
        "brand": facts.get("brand"),
        "ordinary_price": facts.get("ordinary_price"),
        "member_price": facts.get("member_price"),
        "member_program": facts.get("member_program"),
        "member_app": facts.get("member_app"),
        "requires_activation": bool(facts.get("requires_activation")),
        "before_price": facts.get("before_price"),
        "unit_price": facts.get("unit_price"),
        "variants": list(facts.get("variants") or []),
        "identity_confidence": float(facts.get("identity_confidence") or 0),
        "pricing_confidence": float(facts.get("pricing_confidence") or 0),
        "variant_confidence": float(facts.get("variant_confidence") or 0),
    }


def _index_page_pricing_if_safe(
    store: dict[str, Any],
    offer: Offer,
    facts: dict[str, Any],
    *,
    needs_crop: bool,
    page_fingerprint_value: str,
) -> None:
    threshold = float(load_config().get("min_apply_confidence", 0.96))
    if needs_crop:
        return
    if not facts.get("visible") or not _price_relation_valid(facts):
        return
    if float(facts.get("pricing_confidence") or 0) < threshold:
        return

    fingerprint = offer_fingerprint(offer)
    existing = store.setdefault("records", {}).get(fingerprint)
    if isinstance(existing, dict) and existing.get("status") == "completed":
        if existing.get("analysis_level") not in {None, "crop", "page-audit"}:
            return
        if existing.get("analysis_level") in {None, "crop"}:
            return

    signature = offer_pricing_signature(offer)
    store["records"][fingerprint] = {
        "status": "completed",
        "analysis_level": "page-audit",
        "retailer": offer.retailer,
        "publication_id": offer.publication_id,
        "offer_id": offer.id,
        "product_name": offer.product_name,
        "pricing_signature": signature,
        "facts": _pricing_record_facts(facts),
        "page_fingerprint": page_fingerprint_value,
        "updated_at": int(time.time()),
    }
    store.setdefault("pricing_index", {})[signature] = fingerprint


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
        facts_list = _validate_page_output(parsed, {offer.id for offer in candidate.offers})

        store = load_store()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        _record_usage(store, usage, config, kind="page-audit")
        row = store.setdefault("page_audits", {}).setdefault(candidate.fingerprint, record)

        if facts_list is None:
            row.update(
                {
                    "status": "failed",
                    "error": "invalid-structured-output",
                    "updated_at": int(time.time()),
                }
            )
            save_store(store)
            return dict(row)

        threshold = float(config.get("min_apply_confidence", 0.96))
        by_id = {offer.id: offer for offer in candidate.offers}
        crop_needed = 0

        for facts in facts_list:
            offer = by_id[facts["offer_id"]]
            needs_crop = _server_needs_crop(offer, facts, threshold)
            reasons = _crop_reasons(offer, facts, needs_crop)
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
            _index_page_pricing_if_safe(
                store,
                offer,
                facts,
                needs_crop=needs_crop,
                page_fingerprint_value=candidate.fingerprint,
            )

        row.update(
            {
                "status": "completed",
                "model": body.get("model") or config.get("model"),
                "response_id": body.get("id"),
                "usage": usage,
                "audited_offers": len(facts_list),
                "crop_needed": crop_needed,
                "updated_at": int(time.time()),
            }
        )
        store.setdefault("events", []).append(
            {
                "at": int(time.time()),
                "event": "page-audit",
                "page_fingerprint": candidate.fingerprint,
                "status": "completed",
                "retailer": candidate.publication.retailer,
                "page_number": candidate.page_number,
                "offer_count": len(candidate.offers),
                "crop_needed": crop_needed,
            }
        )
        save_store(store)
        return dict(row)
    except Exception as exc:
        store = load_store()
        row = store.setdefault("page_audits", {}).setdefault(candidate.fingerprint, record)
        row.update(
            {
                "status": "failed",
                "error": str(exc)[:500],
                "updated_at": int(time.time()),
            }
        )
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


def collect_crop_candidates(publications: Iterable[Publication]) -> list[CropCandidate]:
    store = load_store()
    semantic = store.get("semantic_facts", {})
    records = store.get("records", {})
    result: list[CropCandidate] = []

    for publication in publications:
        if publication.status == "expired":
            continue
        for offer in publication.structured_offers:
            semantic_row = semantic.get(offer_key(offer))
            if not isinstance(semantic_row, dict) or semantic_row.get("source") != "page-audit":
                continue
            if not semantic_row.get("needs_crop"):
                continue
            fingerprint = offer_fingerprint(offer)
            existing = records.get(fingerprint)
            if isinstance(existing, dict) and existing.get("status") in {"completed", "no-change", "pending"}:
                if existing.get("analysis_level") in {None, "crop"}:
                    continue
            reasons = tuple(
                str(reason)
                for reason in semantic_row.get("crop_reasons", [])
                if str(reason)
            ) or ("page-audit-needs-crop",)
            result.append(
                CropCandidate(
                    fingerprint=fingerprint,
                    publication=publication,
                    offer=offer,
                    page_fingerprint=str(semantic_row.get("page_fingerprint") or ""),
                    reasons=reasons,
                )
            )

    return sorted(
        result,
        key=lambda item: (
            item.offer.retailer.casefold(),
            item.offer.page_number or 0,
            item.offer.product_name.casefold(),
        ),
    )


def _crop_image(candidate: CropCandidate) -> tuple[str | None, str]:
    offer = candidate.offer
    page = None
    if offer.page_number is not None and 0 < offer.page_number <= len(candidate.publication.page_image_urls):
        page = candidate.publication.page_image_urls[offer.page_number - 1]
    if offer.image_url and offer.image_url != page and offer.quality_source == "tjek-catalog":
        return offer.image_url, "high"
    return page or offer.image_url, "high"


def _crop_context(candidate: CropCandidate) -> dict[str, Any]:
    offer = candidate.offer
    page_row = load_store().get("semantic_facts", {}).get(offer_key(offer), {})
    page_facts = page_row.get("facts") if isinstance(page_row, dict) else None
    return {
        "retailer": offer.retailer,
        "publication": offer.publication_title,
        "target_offer_id": offer.id,
        "target_product": offer.product_name,
        "provider_price": offer.price,
        "provider_reference_price": offer.normal_price,
        "provider_unit_price": offer.unit_price,
        "provider_variants": [variant.name for variant in offer.variants],
        "provider_text": offer.raw_text[:1800],
        "target_hotspot_normalized": _box(offer),
        "page_audit_facts": page_facts,
        "verification_reasons": list(candidate.reasons),
    }


def _crop_instructions() -> str:
    return (
        "You are Kurv's targeted flyer verification layer. Inspect ONLY the advert inside the "
        "target hotspot. The full page may contain neighbouring offers; never copy their prices, "
        "badges, variants or legal text. Resolve the page-audit uncertainty. ordinary_price is "
        "the non-member price for this exact campaign; member_price is only an explicitly tied "
        "membership/app/club/plus price. kg/l/100g/100ml prices, deposits, membership fees, "
        "before-prices and package counts are separate metadata. package_size may contain weight/"
        "volume/pack count but must never become a variant. variants must be concrete named "
        "choices only. multiple_products is true if the campaign contains multiple concrete "
        "products/variants even if every name cannot be read. If an important fact remains "
        "uncertain, return null/empty with lower confidence instead of guessing."
    )


def _crop_prompt(candidate: CropCandidate) -> str:
    return _crop_instructions() + "\n\n" + json.dumps(
        _crop_context(candidate), ensure_ascii=False, separators=(",", ":")
    )


def _crop_request_body(candidate: CropCandidate, config: dict[str, Any]) -> dict[str, Any]:
    image_url, detail = _crop_image(candidate)
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": json.dumps(_crop_context(candidate), ensure_ascii=False, separators=(",", ":")),
    }]
    if image_url:
        content.append({"type": "input_image", "image_url": image_url, "detail": detail})
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [
            {"role": "developer", "content": [{
                "type": "input_text",
                "text": _crop_instructions(),
            }]},
            {"role": "user", "content": content},
        ],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_crop_facts",
                "strict": True,
                "schema": _fact_schema(include_offer_id=False),
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
        facts = _validate_fact_row(parsed)

        store = load_store()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        _record_usage(store, usage, config, kind="crop")
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)

        if facts is None:
            row.update(
                {
                    "status": "failed",
                    "error": "invalid-structured-output",
                    "updated_at": int(time.time()),
                }
            )
            save_store(store)
            return dict(row)

        facts["same_offer"] = bool(facts.get("visible"))
        threshold = float(config.get("min_apply_confidence", 0.96))
        useful = bool(facts.get("visible")) and (
            float(facts.get("pricing_confidence") or 0) >= threshold
            or float(facts.get("variant_confidence") or 0) >= threshold
            or float(facts.get("identity_confidence") or 0) >= threshold
        )
        status = "completed" if useful else "no-change"
        row.update(
            {
                "status": status,
                "facts": _pricing_record_facts(facts),
                "semantic_facts": facts,
                "model": body.get("model") or config.get("model"),
                "response_id": body.get("id"),
                "usage": usage,
                "updated_at": int(time.time()),
            }
        )
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

        store.setdefault("events", []).append(
            {
                "at": int(time.time()),
                "event": "crop-analysis",
                "fingerprint": candidate.fingerprint,
                "status": status,
                "retailer": candidate.offer.retailer,
                "page_number": candidate.offer.page_number,
            }
        )
        save_store(store)
        return dict(row)
    except Exception as exc:
        store = load_store()
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)
        row.update(
            {
                "status": "failed",
                "error": str(exc)[:500],
                "updated_at": int(time.time()),
            }
        )
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


def semantic_facts_for_offer(offer: Offer) -> dict[str, Any] | None:
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return None
    row = load_store().get("semantic_facts", {}).get(offer_key(offer))
    if not isinstance(row, dict):
        return None
    facts = row.get("facts")
    if not isinstance(facts, dict) or not facts.get("visible"):
        return None
    return dict(facts)


def semantic_status_payload() -> dict[str, Any]:
    store = load_store()
    page_counts: dict[str, int] = {}
    for row in store.get("page_audits", {}).values():
        if isinstance(row, dict):
            key = str(row.get("status") or "unknown")
            page_counts[key] = page_counts.get(key, 0) + 1
    sources: dict[str, int] = {}
    crops_pending = 0
    for row in store.get("semantic_facts", {}).values():
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "unknown")
        sources[source] = sources.get(source, 0) + 1
        if row.get("needs_crop"):
            crops_pending += 1

    from .luna_enrichment import month_key
    usage = store.get("usage", {}).get(month_key(), {})
    by_kind = usage.get("by_kind") if isinstance(usage.get("by_kind"), dict) else {}
    return {
        "page_audits": page_counts,
        "semantic_offer_facts": sources,
        "crop_pending": crops_pending,
        "usage_by_kind": by_kind,
    }

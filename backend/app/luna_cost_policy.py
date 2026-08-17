from __future__ import annotations

"""Cost-first policy for Kurv's visual flyer intelligence.

The expensive Build 58 semantic audit proved the architecture, but it also
showed that proactively perfecting every multi-product campaign is not a good
use of money. This module keeps autonomous visual coverage while making the
first pass deliberately small:

* every new/changed page can still be visually checked;
* the page result uses a compact schema and low-detail image by default;
* proactive crops are reserved for pricing/member-role uncertainty;
* variant-only uncertainty blocks direct-add through ``multiple_products`` but
  does not spend another request automatically.

It patches the Build 58 semantic engine at import time without changing stored
provider facts. Luna OFF remains authoritative in luna_enrichment/load_config.
"""

import json
from typing import Any

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_config


_INSTALLED = False


def _detail(config: dict[str, Any]) -> str:
    value = str(config.get("page_scout_image_detail", "low")).casefold()
    return value if value in {"low", "high", "auto"} else "low"


def _compact_schema(candidate: semantic.PageAuditCandidate) -> dict[str, Any]:
    ids = [offer.id for offer in candidate.offers]
    row = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "enum": ids},
            "vis": {"type": "boolean"},
            "o": {"type": ["number", "null"]},
            "m": {"type": ["number", "null"]},
            "p": {"type": ["string", "null"]},
            "a": {"type": "boolean"},
            "x": {"type": "boolean"},
            "v": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "pc": {"type": "number", "minimum": 0, "maximum": 1},
            "vc": {"type": "number", "minimum": 0, "maximum": 1},
            "r": {
                "type": "string",
                "enum": ["none", "price", "member", "overlap", "variant", "identity"],
            },
        },
        "required": ["id", "vis", "o", "m", "p", "a", "x", "v", "pc", "vc", "r"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "offers": {
                "type": "array",
                "items": row,
                "minItems": len(ids),
                "maxItems": len(ids),
            }
        },
        "required": ["offers"],
    }


def _compact_context(candidate: semantic.PageAuditCandidate) -> dict[str, Any]:
    targets = []
    for offer in candidate.offers:
        targets.append({
            "id": offer.id,
            "n": offer.product_name[:120],
            "p": offer.price,
            "np": offer.normal_price,
            "v": [variant.name for variant in offer.variants][:6],
            "vc": round(float(offer.variant_confidence or 0), 2),
            "t": (offer.raw_text or "")[:180],
            "b": semantic._box(offer),
        })
    return {
        "retailer": candidate.publication.retailer,
        "page": candidate.page_number,
        "targets": targets,
    }


def _page_scout_prompt(candidate: semantic.PageAuditCandidate) -> str:
    return (
        "Kurv flyer scout. Inspect only the listed hotspot adverts on this page. "
        "Return exactly one row for every id. Never borrow facts from neighbours. "
        "Keys: o=ordinary non-member campaign price; m=explicit member/app/plus price; "
        "p=advertised member programme; a=explicit activation/coupon required; "
        "x=more than one concrete product/variant in the campaign; v=only concrete "
        "named variants (never weight/volume/pack size/generic 'flere varianter'); "
        "pc/vc=confidence for price roles/variants. Unit prices, deposits, before-prices, "
        "membership fees, weights and pack counts are never o/m or variants. "
        "r is the single main reason a high-detail check would help: price, member, "
        "overlap, variant, identity, or none. Use null/empty and lower confidence rather "
        "than guessing. vis=false if the target advert cannot be associated safely.\n"
        + json.dumps(_compact_context(candidate), ensure_ascii=False, separators=(",", ":"))
    )


def _cost_page_request_body(candidate: semantic.PageAuditCandidate, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": _page_scout_prompt(candidate)},
                {"type": "input_image", "image_url": candidate.image_url, "detail": _detail(config)},
            ],
        }],
        "reasoning": {"effort": "low"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_page_scout",
                "strict": True,
                "schema": _compact_schema(candidate),
            },
        },
        "max_output_tokens": int(config.get("page_scout_max_output_tokens", 1400)),
    }


def _expanded_row(raw: object, allowed_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required = {"id", "vis", "o", "m", "p", "a", "x", "v", "pc", "vc", "r"}
    if not required.issubset(raw):
        return None
    offer_id = raw.get("id")
    if not isinstance(offer_id, str) or offer_id not in allowed_ids:
        return None
    if not isinstance(raw.get("v"), list) or not all(isinstance(item, str) for item in raw["v"]):
        return None
    for key in ("pc", "vc"):
        score = raw.get(key)
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            return None
    reason = raw.get("r")
    if reason not in {"none", "price", "member", "overlap", "variant", "identity"}:
        return None

    variants = []
    for item in raw["v"]:
        name = semantic._safe_variant(item)
        if name and name not in variants:
            variants.append(name)

    return {
        "offer_id": offer_id,
        "visible": bool(raw.get("vis")),
        "product_name": None,
        "brand": None,
        "ordinary_price": raw.get("o"),
        "member_price": raw.get("m"),
        "member_program": raw.get("p"),
        "member_app": None,
        "requires_activation": bool(raw.get("a")),
        "before_price": None,
        "unit_price": None,
        "package_size": None,
        "multiple_products": bool(raw.get("x")),
        "variants": variants[:8],
        "identity_confidence": 0.0,
        "pricing_confidence": float(raw.get("pc") or 0),
        "variant_confidence": float(raw.get("vc") or 0),
        "needs_crop_verification": reason != "none",
        "_deep_reason": reason,
        "_analysis_mode": "page-scout",
    }


def _cost_validate_page_output(value: object, allowed_ids: set[str]) -> list[dict[str, Any]] | None:
    if not isinstance(value, dict) or not isinstance(value.get("offers"), list):
        return None
    rows = []
    seen: set[str] = set()
    for raw in value["offers"]:
        row = _expanded_row(raw, allowed_ids)
        if row is None or row["offer_id"] in seen:
            return None
        seen.add(row["offer_id"])
        rows.append(row)
    # Never mark a page completed with an unaudited hotspot.
    if seen != set(allowed_ids):
        return None
    return rows


def _provider_price_conflict(offer, facts: dict[str, Any], threshold: float) -> bool:
    visual_prices = {
        float(value)
        for value in (facts.get("ordinary_price"), facts.get("member_price"))
        if isinstance(value, (int, float))
    }
    return bool(
        offer.price is not None
        and float(facts.get("pricing_confidence") or 0) >= threshold
        and visual_prices
        and all(abs(float(offer.price) - value) > 0.005 for value in visual_prices)
    )


def _cost_server_needs_crop(offer, facts: dict[str, Any], threshold: float) -> bool:
    # Invisible/identity-only/variant-only uncertainty is not worth a proactive
    # paid request. multiple_products still reaches iOS as a direct-add blocker.
    if not facts.get("visible"):
        return False
    if not semantic._price_relation_valid(facts):
        return True

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    if facts.get("member_price") is not None and pricing_conf < threshold:
        return True
    if facts.get("member_program") and facts.get("member_price") is None:
        return True
    if _provider_price_conflict(offer, facts, threshold):
        return True

    reason = str(facts.get("_deep_reason") or "none")
    if reason in {"price", "member", "overlap"}:
        return True

    config = load_config()
    if config.get("proactive_variant_crops", False) and facts.get("multiple_products"):
        return (
            float(facts.get("variant_confidence") or 0) < 0.99
            or len(facts.get("variants") or []) < 2
        )
    return False


def _cost_crop_reasons(offer, facts: dict[str, Any], needs_crop: bool) -> list[str]:
    if not needs_crop:
        return []
    result: list[str] = []
    reason = str(facts.get("_deep_reason") or "none")
    if reason in {"price", "member", "overlap"}:
        result.append(f"page-scout-{reason}-uncertain")
    if not semantic._price_relation_valid(facts):
        result.append("page-scout-price-role-conflict")
    if facts.get("member_program") and facts.get("member_price") is None:
        result.append("page-scout-member-price-missing")
    if _provider_price_conflict(
        offer,
        facts,
        float(load_config().get("min_apply_confidence", 0.96)),
    ):
        result.append("page-scout-provider-price-conflict")
    if load_config().get("proactive_variant_crops", False) and facts.get("multiple_products"):
        result.append("page-scout-variant-uncertain")
    return list(dict.fromkeys(result)) or ["page-scout-pricing-review"]


def status_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "page_mode": "compact-scout",
        "page_image_detail": _detail(config),
        "page_scout_max_output_tokens": int(config.get("page_scout_max_output_tokens", 1400)),
        "proactive_variant_crops": bool(config.get("proactive_variant_crops", False)),
        "recommended_monthly_budget_dkk": float(config.get("recommended_monthly_budget_dkk", 20.0)),
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    semantic._page_request_body = _cost_page_request_body
    semantic._validate_page_output = _cost_validate_page_output
    semantic._server_needs_crop = _cost_server_needs_crop
    semantic._crop_reasons = _cost_crop_reasons
    _INSTALLED = True

from __future__ import annotations

"""Ultra-compact cost policy for Kurv's visual flyer intelligence.

Build 58 proved that a full-page visual pass can autonomously discover facts
that providers omit (for example a visual-only member price). Build 59 reduced
image detail and removed proactive variant crops, but the first live shadow test
still spent too many output/reasoning tokens and unnecessarily requested a crop
for an already-safe member price.

This v2 policy keeps the valuable always-on visual coverage while making the
background pass pricing-first:

* low-detail page image;
* very short hotspot ids;
* only ordinary/member price, programme, activation, multi-product safety and
  pricing confidence are returned;
* no product/brand/weight/variant-name output in the background pass;
* variant-only uncertainty never creates a proactive crop;
* a correctly read high-confidence member price never creates a crop merely
  because the model noticed membership;
* deep crop analysis remains available for actual price/member ambiguity.

Provider facts remain untouched. Luna OFF remains authoritative in
``luna_enrichment.load_config``.
"""

import json
from typing import Any, Iterable

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_config


_INSTALLED = False


def _detail(config: dict[str, Any]) -> str:
    value = str(config.get("page_scout_image_detail", "low")).casefold()
    return value if value in {"low", "high", "auto"} else "low"


def _reasoning_effort(config: dict[str, Any]) -> str:
    value = str(config.get("page_scout_reasoning_effort", "minimal")).casefold()
    return value if value in {"minimal", "low"} else "minimal"


def _output_cap(config: dict[str, Any]) -> int:
    # Cost guard: persisted Build59 config may still say 1400. v2 deliberately
    # caps the cheap scout at 700 even before config migration.
    return min(700, max(256, int(config.get("page_scout_max_output_tokens", 700))))


def _short_id_map(ids: Iterable[str]) -> dict[str, str]:
    """Map full offer ids to the shortest stable unique prefix on the page."""
    values = sorted(set(str(value) for value in ids))
    result: dict[str, str] = {}
    for value in values:
        chosen = value
        for length in range(2, min(12, len(value)) + 1):
            prefix = value[:length]
            if sum(other.startswith(prefix) for other in values) == 1:
                chosen = prefix
                break
        result[value] = chosen
    return result


def _short_reverse(ids: Iterable[str]) -> dict[str, str]:
    mapping = _short_id_map(ids)
    reverse: dict[str, str] = {}
    for full, short in mapping.items():
        if short in reverse:
            return {}
        reverse[short] = full
    return reverse


def _compact_schema(candidate: semantic.PageAuditCandidate) -> dict[str, Any]:
    short_ids = list(_short_id_map(offer.id for offer in candidate.offers).values())
    row = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "i": {"type": "string", "enum": short_ids},
            "o": {"type": ["number", "null"]},
            "m": {"type": ["number", "null"]},
            "p": {"type": ["string", "null"]},
            "a": {"type": "boolean"},
            "x": {"type": "boolean"},
            "c": {"type": "number", "minimum": 0, "maximum": 1},
            "q": {"type": "boolean"},
        },
        "required": ["i", "o", "m", "p", "a", "x", "c", "q"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "r": {
                "type": "array",
                "items": row,
                "minItems": len(short_ids),
                "maxItems": len(short_ids),
            }
        },
        "required": ["r"],
    }


def _compact_context(candidate: semantic.PageAuditCandidate) -> dict[str, Any]:
    ids = _short_id_map(offer.id for offer in candidate.offers)
    targets = []
    for offer in candidate.offers:
        targets.append({
            "i": ids[offer.id],
            "n": offer.product_name[:80],
            "p": offer.price,
            "np": offer.normal_price,
            "t": (offer.raw_text or "")[:120],
            "b": semantic._box(offer),
        })
    return {"s": candidate.publication.retailer, "t": targets}


def _page_scout_prompt(candidate: semantic.PageAuditCandidate) -> str:
    return (
        "Kurv price scout. Inspect ONLY each listed hotspot advert. Return exactly one "
        "row per short id. Never borrow from neighbours. o=ordinary non-member campaign "
        "price; m=explicit member/app/plus price; p=visible member programme; a=true only "
        "for explicit activate/clip/coupon action; x=true if the advert clearly covers more "
        "than one concrete product/variant; c=confidence that o/m roles belong to this hotspot; "
        "q=true ONLY when price/member role or hotspot association is too ambiguous for safe use. "
        "Unit prices, deposits, before-prices, membership fees, weights and pack counts are never "
        "o/m. Do not return product names, brands, weights or variant names. Null beats guessing.\n"
        + json.dumps(_compact_context(candidate), ensure_ascii=False, separators=(",", ":"))
    )


def _cost_page_request_body(candidate: semantic.PageAuditCandidate, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": str(config.get("page_scout_model") or config.get("model") or "gpt-5.6-luna"),
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": _page_scout_prompt(candidate)},
                {"type": "input_image", "image_url": candidate.image_url, "detail": _detail(config)},
            ],
        }],
        "reasoning": {"effort": _reasoning_effort(config)},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_price_scout_v2",
                "strict": True,
                "schema": _compact_schema(candidate),
            },
        },
        "max_output_tokens": _output_cap(config),
    }


def _expanded_row(raw: object, allowed_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required = {"i", "o", "m", "p", "a", "x", "c", "q"}
    if not required.issubset(raw):
        return None

    reverse = _short_reverse(allowed_ids)
    short_id = raw.get("i")
    if not isinstance(short_id, str) or short_id not in reverse:
        return None

    confidence = raw.get("c")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return None

    for key in ("o", "m"):
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0
        ):
            return None

    programme = raw.get("p")
    if programme is not None and not isinstance(programme, str):
        return None

    return {
        "offer_id": reverse[short_id],
        "visible": True,
        "product_name": None,
        "brand": None,
        "ordinary_price": raw.get("o"),
        "member_price": raw.get("m"),
        "member_program": programme.strip() if isinstance(programme, str) and programme.strip() else None,
        "member_app": None,
        "requires_activation": bool(raw.get("a")),
        "before_price": None,
        "unit_price": None,
        "package_size": None,
        "multiple_products": bool(raw.get("x")),
        "variants": [],
        "identity_confidence": 0.0,
        "pricing_confidence": float(confidence),
        "variant_confidence": 0.0,
        "needs_crop_verification": bool(raw.get("q")),
        "_price_ambiguous": bool(raw.get("q")),
        "_analysis_mode": "ultracompact-price-scout-v2",
    }


def _cost_validate_page_output(value: object, allowed_ids: set[str]) -> list[dict[str, Any]] | None:
    if not isinstance(value, dict) or not isinstance(value.get("r"), list):
        return None
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value["r"]:
        row = _expanded_row(raw, allowed_ids)
        if row is None or row["offer_id"] in seen:
            return None
        seen.add(row["offer_id"])
        rows.append(row)
    # Coverage contract: a page is never completed with a missing hotspot.
    if seen != set(allowed_ids):
        return None
    return rows


def _provider_price_conflict(offer, facts: dict[str, Any], threshold: float) -> bool:
    visual_prices = {
        float(value)
        for value in (facts.get("ordinary_price"), facts.get("member_price"))
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return bool(
        offer.price is not None
        and float(facts.get("pricing_confidence") or 0) >= threshold
        and visual_prices
        and all(abs(float(offer.price) - value) > 0.005 for value in visual_prices)
    )


def _cost_server_needs_crop(offer, facts: dict[str, Any], threshold: float) -> bool:
    # Background scout only escalates actual pricing/member ambiguity.
    if not semantic._price_relation_valid(facts):
        return True

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    if facts.get("member_price") is not None and pricing_conf < threshold:
        return True
    if facts.get("member_program") and facts.get("member_price") is None:
        return True
    if _provider_price_conflict(offer, facts, threshold):
        return True
    if facts.get("_price_ambiguous"):
        # A model ambiguity flag is ignored when the complete member relation is
        # already safe at high confidence. This prevents the live Becel case
        # (15/12 at 0.99) from paying for a redundant crop.
        complete_member = (
            facts.get("member_price") is not None
            and facts.get("ordinary_price") is not None
            and pricing_conf >= threshold
            and semantic._price_relation_valid(facts)
        )
        return not complete_member
    return False


def _cost_crop_reasons(offer, facts: dict[str, Any], needs_crop: bool) -> list[str]:
    if not needs_crop:
        return []
    result: list[str] = []
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
    if facts.get("_price_ambiguous"):
        result.append("page-scout-price-association-uncertain")
    return list(dict.fromkeys(result)) or ["page-scout-pricing-review"]


def status_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "page_mode": "ultracompact-price-scout-v2",
        "page_image_detail": _detail(config),
        "page_scout_model": str(config.get("page_scout_model") or config.get("model") or "gpt-5.6-luna"),
        "page_scout_reasoning_effort": _reasoning_effort(config),
        "page_scout_max_output_tokens": _output_cap(config),
        "proactive_variant_crops": False,
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

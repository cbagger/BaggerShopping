from __future__ import annotations

"""Quality-first cost policy for Kurv's visual flyer intelligence.

The live Build58/59 shadow tests established the final operating principle:

* keep the rich high-detail page audit because its semantic facts materially
  improve Kurv's pricing, identity and variant engines;
* price/member safety always has first priority;
* a visual-only member price is independently verified by one targeted crop;
* variant crops are useful when a campaign is clearly multi-product but the
  rich page pass still cannot provide usable choices;
* already useful page variants must not trigger a second paid request merely to
  chase a near-perfect confidence score;
* variant enrichment is the first paid work to stop when its own monthly slice
  is exhausted. The global Luna hard budget remains authoritative.

The generic semantic-sanity layer owns the invariants for price roles. This
module may prioritise or suppress optional work, but it must never overwrite
those safety reasons when it installs its worker-level crop gate.
"""

from typing import Any, Iterable

from . import luna_semantic_audit as semantic
from . import luna_semantic_guards as semantic_guards
from .luna_enrichment import load_config, load_store
from .member_pricing import has_membership_signal


_INSTALLED = False
_VARIANT_REASON = "page-audit-variant-enrichment"


def _provider_has_member_evidence(offer: Any) -> bool:
    text = " ".join(
        value
        for value in (getattr(offer, "product_name", None), getattr(offer, "raw_text", None))
        if isinstance(value, str) and value.strip()
    )
    return has_membership_signal(text)


def _visual_prices(facts: dict[str, Any]) -> set[float]:
    return {
        float(value)
        for value in (facts.get("ordinary_price"), facts.get("member_price"))
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) > 0
    }


def _provider_price_conflict(offer: Any, facts: dict[str, Any], threshold: float) -> bool:
    prices = _visual_prices(facts)
    provider_price = getattr(offer, "price", None)
    return bool(
        provider_price is not None
        and float(facts.get("pricing_confidence") or 0) >= threshold
        and prices
        and all(abs(float(provider_price) - value) > 0.005 for value in prices)
    )


def _pricing_is_safe(offer: Any, facts: dict[str, Any], threshold: float) -> bool:
    if not facts.get("visible"):
        return False
    if semantic_guards._pricing_sanity_reasons(offer, facts):
        return False
    if not semantic._price_relation_valid(facts):
        return False

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    if pricing_conf < threshold:
        return False
    if _provider_price_conflict(offer, facts, threshold):
        return False

    member = facts.get("member_price")
    ordinary = facts.get("ordinary_price")
    programme = facts.get("member_program")

    if member is not None:
        if ordinary is None:
            return False
        # A visual-only member price is valuable but is also the exact failure
        # shape observed when a neighbouring Becel badge leaked into Actimel.
        if not _provider_has_member_evidence(offer):
            return False
    elif programme:
        return False

    return True


def _variant_threshold(config: dict[str, Any] | None = None) -> float:
    config = config or load_config()
    value = float(config.get("variant_crop_confidence_threshold", 0.80))
    return min(0.99, max(0.0, value))


def _variant_enrichment_needed(
    facts: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> bool:
    config = config or load_config()
    if not bool(config.get("selective_variant_crops", True)):
        return False
    if not facts.get("visible") or not facts.get("multiple_products"):
        return False

    variants = [
        value.strip()
        for value in (facts.get("variants") or [])
        if isinstance(value, str) and value.strip()
    ]
    confidence = float(facts.get("variant_confidence") or 0)

    return len(variants) < 2 or confidence < _variant_threshold(config)


def _pricing_crop_needed(offer: Any, facts: dict[str, Any], threshold: float) -> bool:
    # Cost/quality policy must preserve every generic safety invariant installed
    # by luna_semantic_guards. This is deliberately first: price safety always
    # wins over optional-work suppression and budget prioritisation.
    if semantic_guards._pricing_sanity_reasons(offer, facts):
        return True
    if not facts.get("visible"):
        return True
    if not semantic._price_relation_valid(facts):
        return True

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    if facts.get("member_price") is not None and pricing_conf < threshold:
        return True
    if facts.get("member_program") and facts.get("member_price") is None:
        return True
    if _provider_price_conflict(offer, facts, threshold):
        return True
    if facts.get("member_price") is not None and not _provider_has_member_evidence(offer):
        return True

    if facts.get("needs_crop_verification") and not _pricing_is_safe(offer, facts, threshold):
        return True
    return False


def _balanced_server_needs_crop(offer: Any, facts: dict[str, Any], threshold: float) -> bool:
    if _pricing_crop_needed(offer, facts, threshold):
        return True
    return _variant_enrichment_needed(facts)


def _balanced_crop_reasons(offer: Any, facts: dict[str, Any], needs_crop: bool) -> list[str]:
    if not needs_crop:
        return []

    config = load_config()
    threshold = float(config.get("min_apply_confidence", 0.96))

    # Keep the exact generic sanity reason in the persisted crop record. This
    # gives targeted-crop prompts and diagnostics the same explanation that
    # caused the worker-level gate to fire.
    result: list[str] = list(semantic_guards._pricing_sanity_reasons(offer, facts))

    if not facts.get("visible"):
        result.append("page-audit-target-not-visible")
    if not semantic._price_relation_valid(facts):
        result.append("page-audit-price-role-conflict")

    pricing_conf = float(facts.get("pricing_confidence") or 0)
    if facts.get("member_price") is not None and pricing_conf < threshold:
        result.append("page-audit-member-price-low-confidence")
    if facts.get("member_program") and facts.get("member_price") is None:
        result.append("page-audit-member-price-missing")
    if _provider_price_conflict(offer, facts, threshold):
        result.append("page-audit-provider-price-conflict")
    if facts.get("member_price") is not None and not _provider_has_member_evidence(offer):
        result.append("page-audit-new-member-price-verification")
    if facts.get("needs_crop_verification") and not _pricing_is_safe(offer, facts, threshold):
        result.append("page-audit-pricing-association-uncertain")

    if _variant_enrichment_needed(facts, config):
        result.append(_VARIANT_REASON)

    return list(dict.fromkeys(result)) or ["page-audit-pricing-review"]


def is_variant_only_crop(candidate_or_reasons: Any) -> bool:
    reasons = getattr(candidate_or_reasons, "reasons", candidate_or_reasons)
    values = {str(reason) for reason in (reasons or ()) if str(reason)}
    return bool(values) and values == {_VARIANT_REASON}


def sort_crop_candidates(candidates: Iterable[Any]) -> list[Any]:
    """Pricing/member verification first; optional variant enrichment last."""
    return sorted(
        candidates,
        key=lambda item: (
            1 if is_variant_only_crop(item) else 0,
            item.offer.retailer.casefold(),
            item.offer.page_number or 0,
            item.offer.product_name.casefold(),
        ),
    )


def variant_crop_spend_dkk(
    config: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> float:
    config = config or load_config()
    store = store or load_store()
    total = 0.0
    for row in store.get("records", {}).values():
        if not isinstance(row, dict):
            continue
        if row.get("analysis_level") != "crop" or row.get("status") != "completed":
            continue
        if not is_variant_only_crop(row.get("reasons") or ()):
            continue
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        total += semantic._usage_cost_dkk(usage, config)
    return round(total, 6)


def variant_crop_budget_allows(
    config: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> bool:
    config = config or load_config()
    if not bool(config.get("selective_variant_crops", True)):
        return False
    cap = max(0.0, float(config.get("variant_crop_max_monthly_dkk", 5.0)))
    return variant_crop_spend_dkk(config, store) < cap


def status_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "page_mode": "rich-page-audit-quality-first-v4",
        "page_image_detail": "high",
        "page_reasoning_effort": "low",
        "selective_variant_crops": bool(config.get("selective_variant_crops", True)),
        "variant_crop_confidence_threshold": _variant_threshold(config),
        "variant_crop_max_monthly_dkk": float(config.get("variant_crop_max_monthly_dkk", 5.0)),
        "variant_crop_spend_dkk": variant_crop_spend_dkk(config),
        "variant_crop_budget_available": variant_crop_budget_allows(config),
        "visual_only_member_price_requires_crop": True,
        "generic_member_price_sanity_preserved": True,
        "recommended_monthly_budget_dkk": float(config.get("recommended_monthly_budget_dkk", 20.0)),
        "current_luna_input_usd_per_million": 0.20,
        "current_luna_output_usd_per_million": 1.20,
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # The cost layer owns the final worker-level gate, but explicitly delegates
    # all price-role invariants to luna_semantic_guards above.
    semantic._server_needs_crop = _balanced_server_needs_crop
    semantic._crop_reasons = _balanced_crop_reasons
    _INSTALLED = True

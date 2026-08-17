from __future__ import annotations

"""Quality-first cost policy for Kurv's visual flyer intelligence.

Live shadow tests changed the cost/quality trade-off:

* GPT-5.6 Luna's current API price is low enough that a rich high-detail page
  audit is affordable for Kurv's weekly flyer volume.
* Removing reasoning made the model *less* safe: a neighbouring Bilka Plus
  price leaked into Actimel even though the request became no cheaper.
* Variant-only proactive crops are still unnecessary. The rich page audit can
  preserve useful semantic facts for the engines while ``multiple_products``
  blocks unsafe direct-add.
* A member price discovered visually without provider membership evidence is
  valuable, but it must be independently verified by a targeted crop before it
  may become authoritative. This converts neighbour leakage into a safe,
  relatively rare second-pass request.

This policy deliberately leaves Build 58's rich page request/schema/validator
untouched. It patches only the server-side crop gate and crop reasons. Provider
facts remain untouched and Luna OFF remains authoritative.
"""

from typing import Any

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_config
from .member_pricing import has_membership_signal


_INSTALLED = False


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
        # A visual-only member price is exactly the kind of valuable fact Luna
        # exists to discover, but it is also the failure shape observed in the
        # no-reasoning Actimel test. Require one targeted crop before applying.
        if not _provider_has_member_evidence(offer):
            return False
    elif programme:
        return False

    return True


def _balanced_server_needs_crop(offer: Any, facts: dict[str, Any], threshold: float) -> bool:
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

    # New visual-only membership facts are independently verified. This is a
    # small cost compared with allowing a neighbouring badge/price to leak.
    if facts.get("member_price") is not None and not _provider_has_member_evidence(offer):
        return True

    # Build 58's model may request a crop simply because variant names are too
    # small. That must not create an automatic paid request when price roles are
    # already safe. ``multiple_products`` remains available to iOS as the
    # direct-add blocker and the rich facts remain cached for later/on-demand
    # variant work.
    if facts.get("needs_crop_verification") and not _pricing_is_safe(offer, facts, threshold):
        return True

    return False


def _balanced_crop_reasons(offer: Any, facts: dict[str, Any], needs_crop: bool) -> list[str]:
    if not needs_crop:
        return []

    threshold = float(load_config().get("min_apply_confidence", 0.96))
    result: list[str] = []

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

    return list(dict.fromkeys(result)) or ["page-audit-pricing-review"]


def status_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "page_mode": "rich-page-audit-cost-balanced-v3",
        "page_image_detail": "high",
        "page_reasoning_effort": "low",
        "proactive_variant_crops": False,
        "visual_only_member_price_requires_crop": True,
        "recommended_monthly_budget_dkk": float(config.get("recommended_monthly_budget_dkk", 20.0)),
        # Current public GPT-5.6 Luna standard API prices from OpenAI's
        # 2026-07-30 price reduction. Persisted accounting config is migrated
        # explicitly on QNAP so historical usage is not silently rewritten.
        "current_luna_input_usd_per_million": 0.20,
        "current_luna_output_usd_per_million": 1.20,
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Deliberately do NOT patch _page_request_body or _validate_page_output.
    # Build 58's rich high-detail semantic audit is retained.
    semantic._server_needs_crop = _balanced_server_needs_crop
    semantic._crop_reasons = _balanced_crop_reasons
    _INSTALLED = True

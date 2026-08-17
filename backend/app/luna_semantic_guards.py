from __future__ import annotations

"""Safety guards around Kurv's semantic flyer-audit engine.

The invariants are deliberately explicit and independently testable:
1) a page is never accepted as fully audited when Luna omitted a target hotspot;
2) an old Luna result can never block a crop explicitly requested by a newer
   semantic/pricing contract;
3) a safe page result may upgrade legacy pricing while a targeted crop remains
   the strongest cached visual result;
4) provider product prices, member prices and unit/reference prices are separate
   roles and suspicious role assignments always fail closed into visual review;
5) a visible membership-price badge without a resolved member amount must be
   re-read from the exact advert crop instead of being silently ignored.
"""

import hashlib
import re

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_store, offer_fingerprint


# Changing this version intentionally gives active pages a new page fingerprint.
# It is a processing-contract version, not a provider-content version. A one-time
# backfill after deployment therefore re-audits the current flyers with the new
# generic member-price sanity schema without deleting any historic Luna data.
SEMANTIC_AUDIT_CONTRACT_VERSION = "member-price-sanity-v2"

_installed = False
_original_fact_schema = semantic._fact_schema
_original_validate_page_output = semantic._validate_page_output
_original_index_page_pricing_if_safe = semantic._index_page_pricing_if_safe
_original_page_schema = semantic._page_schema
_original_page_prompt = semantic._page_prompt
_original_page_fingerprint = semantic.page_fingerprint
_original_server_needs_crop = semantic._server_needs_crop
_original_crop_prompt = semantic._crop_prompt

_UNIT_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?<!\d)(?P<price>\d{1,4}(?:[,.]\d{1,2})?)\s*kr\.?\s*(?:/|pr\.?)\s*"
        r"(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml|stk\.?|styk(?:ker)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpr\.?\s*(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml|stk\.?|styk(?:ker)?)\b"
        r"[^\d]{0,24}(?P<price>\d{1,4}(?:[,.]\d{1,2})?)",
        re.IGNORECASE,
    ),
)


def _same_numeric_price(left, right, *, tolerance: float = 0.005) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return abs(float(left) - float(right)) <= tolerance


def _unit_price_values(value: object) -> set[float]:
    if not isinstance(value, str) or not value.strip():
        return set()
    result: set[float] = set()
    for pattern in _UNIT_PRICE_PATTERNS:
        for match in pattern.finditer(value):
            try:
                number = float(match.group("price").replace(",", "."))
            except (AttributeError, ValueError):
                continue
            if 0 < number <= 10_000:
                result.add(round(number, 2))
    return result


def _pricing_sanity_reasons(offer, facts) -> tuple[str, ...]:
    """Return generic reasons why visual price roles are not safe yet.

    These checks never invent a replacement price. They only decide whether a
    sharper visual crop is required. This keeps genuinely unusual promotions
    possible while preventing a kg/stk comparison price or an unresolved badge
    from becoming customer-visible truth just because confidence is high.
    """
    if not isinstance(facts, dict) or not (facts.get("visible") or facts.get("same_offer")):
        return ()

    reasons: list[str] = []
    member = facts.get("member_price")
    ordinary = facts.get("ordinary_price")
    member_visible = facts.get("membership_price_visible") is True
    member_program = str(facts.get("member_program") or "").strip()
    member_app = str(facts.get("member_app") or "").strip()

    # Live Seafoodmix regression: repeating the untyped provider primary value
    # as member price while ordinary is unknown has not resolved the roles.
    if ordinary is None and _same_numeric_price(getattr(offer, "price", None), member):
        reasons.append("page-audit-primary-price-role-ambiguous")

    # A model can see a +/Plus/member badge but fail to read the amount. That is
    # exactly when Kurv should spend one targeted crop rather than silently omit
    # the member price (e.g. visual-only + PRIS creatives).
    if member is None and member_visible:
        reasons.append("page-audit-visible-member-price-missing-value")
    elif member is None and (member_program or member_app):
        reasons.append("page-audit-member-program-without-price")

    # Never accept a value that the same visual analysis itself describes as a
    # comparison/unit price. Covers both “166,67 kr/kg” and “Pr. stk. max. 1,98”.
    unit_values = _unit_price_values(facts.get("unit_price"))
    if any(_same_numeric_price(member, value) for value in unit_values):
        reasons.append("page-audit-member-price-is-unit-price")
    if any(_same_numeric_price(ordinary, value) for value in unit_values):
        reasons.append("page-audit-ordinary-price-is-unit-price")

    # Extreme discounts are not rejected; they are merely verified once with a
    # crop. This catches suspicious 85 -> 1.98 shapes even when the unit-price
    # string is formatted in a way the deterministic parser does not recognise.
    reference = ordinary
    if not isinstance(reference, (int, float)) or reference <= 0:
        reference = getattr(offer, "price", None)
    if (
        isinstance(member, (int, float))
        and isinstance(reference, (int, float))
        and reference > member > 0
        and reference - member >= 10
        and member / reference <= 0.25
    ):
        reasons.append("page-audit-extreme-member-discount-needs-verification")

    return tuple(dict.fromkeys(reasons))


def _primary_member_role_ambiguous(offer, facts) -> bool:
    """Backward-compatible helper retained for existing regression tests."""
    return "page-audit-primary-price-role-ambiguous" in _pricing_sanity_reasons(offer, facts)


def _versioned_page_fingerprint(publication, page_number, offers):
    base = _original_page_fingerprint(publication, page_number, offers)
    raw = f"{SEMANTIC_AUDIT_CONTRACT_VERSION}|{base}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _strict_fact_schema(*, include_offer_id: bool, offer_ids=None):
    schema = _original_fact_schema(include_offer_id=include_offer_id, offer_ids=offer_ids)
    properties = schema.setdefault("properties", {})
    properties["membership_price_visible"] = {"type": "boolean"}
    required = schema.setdefault("required", [])
    if "membership_price_visible" not in required:
        required.append("membership_price_visible")
    return schema


def _strict_page_schema(candidate):
    schema = _original_page_schema(candidate)
    count = max(1, len(candidate.offers))
    offers_schema = schema["properties"]["offers"]
    offers_schema["minItems"] = count
    offers_schema["maxItems"] = count
    return schema


def _strict_page_prompt(candidate):
    return _original_page_prompt(candidate) + (
        "\n\nIMPORTANT COVERAGE CONTRACT: Return exactly one result for EVERY target offer_id "
        "listed in the context, no more and no fewer. If a target cannot be safely associated "
        "with a visible advert, still return its exact offer_id with visible=false, null/empty "
        "facts, low confidences, needs_crop_verification=true, and membership_price_visible=false. "
        "Never omit a target."
        "\n\nIMPORTANT GENERIC MEMBER-PRICE CONTRACT: membership_price_visible=true ONLY when "
        "this exact target advert visibly contains a membership/app/club/plus PRICE treatment or "
        "badge (for example + PRIS, PLUS PRIS, MEDLEMSPRIS, 'Med ... Plus' tied to a price). A "
        "general page banner or neighbouring advert does not count. If membership_price_visible is "
        "true but you cannot safely read its amount, return member_price=null and set "
        "needs_crop_verification=true. provider_price is an untyped source value, not evidence that "
        "the value is ordinary_price or member_price. A kg/l/100g/100ml/stk comparison price must "
        "stay only in unit_price and can NEVER equal ordinary_price or member_price. If a proposed "
        "member price is dramatically lower than the ordinary/provider price, re-check whether it "
        "is actually a unit price and request crop verification whenever the role is not visually "
        "unambiguous."
    )


def _strict_crop_prompt(candidate):
    return _original_crop_prompt(candidate) + (
        "\n\nIMPORTANT TARGETED MEMBER-PRICE CONTRACT: Re-read this exact crop from scratch. "
        "Set membership_price_visible=true only when the target advert itself visibly has a "
        "membership/app/club/plus price treatment. Inspect every headline price and bind each to "
        "its visible role label/layout. provider_price is untyped and must never be copied into "
        "member_price merely because Plus/app/member text is visible. kg/l/100g/100ml/stk prices "
        "belong only in unit_price — examples such as 'Pr. stk. max. 1,98' are NOT member prices. "
        "If page_audit_facts proposed a member price that equals a unit/comparison price, or a "
        "visible member badge had no amount, that result is specifically unresolved. When two "
        "customer prices are visible, return the non-member campaign price as ordinary_price and "
        "the explicitly membership-tied campaign price as member_price. If the role still cannot "
        "be read safely, return null and lower pricing_confidence rather than guessing."
    )


def _strict_validate_page_output(value, allowed_ids):
    rows = _original_validate_page_output(value, allowed_ids)
    if rows is None:
        return None
    returned_ids = {row.get("offer_id") for row in rows}
    if returned_ids != set(allowed_ids):
        return None
    for row in rows:
        row["membership_price_visible"] = bool(row.get("membership_price_visible"))
    return rows


def _strict_server_needs_crop(offer, facts, threshold):
    if _pricing_sanity_reasons(offer, facts):
        return True
    return _original_server_needs_crop(offer, facts, threshold)


def _index_page_pricing_upgrading_legacy(
    store,
    offer,
    facts,
    *,
    needs_crop,
    page_fingerprint_value,
):
    fingerprint = offer_fingerprint(offer)
    existing = store.setdefault("records", {}).get(fingerprint)
    if (
        isinstance(existing, dict)
        and existing.get("status") == "completed"
        and existing.get("analysis_level") is None
    ):
        store["records"].pop(fingerprint, None)
    return _original_index_page_pricing_if_safe(
        store,
        offer,
        facts,
        needs_crop=needs_crop,
        page_fingerprint_value=page_fingerprint_value,
    )


def _crop_candidates_allowing_build58_reverification(publications):
    store = load_store()
    semantic_rows = store.get("semantic_facts", {})
    records = store.get("records", {})
    result = []

    for publication in publications:
        if publication.status == "expired":
            continue
        for offer in publication.structured_offers:
            semantic_row = semantic_rows.get(semantic.offer_key(offer))
            if not isinstance(semantic_row, dict):
                continue
            if semantic_row.get("source") not in {"page-audit", "crop"}:
                continue

            facts = semantic_row.get("facts")
            sanity_reasons = list(_pricing_sanity_reasons(offer, facts))
            needs_crop = bool(semantic_row.get("needs_crop")) or bool(sanity_reasons)
            if not needs_crop:
                continue

            fingerprint = offer_fingerprint(offer)
            existing = records.get(fingerprint)
            existing_facts = existing.get("facts") if isinstance(existing, dict) else None
            existing_anomalous = bool(_pricing_sanity_reasons(offer, existing_facts))

            # A safe completed targeted crop remains final. A crop that itself
            # still has a generic pricing anomaly may be retried under the new
            # contract instead of permanently poisoning the pricing index.
            if (
                isinstance(existing, dict)
                and existing.get("analysis_level") == "crop"
                and existing.get("status") in {"completed", "no-change", "pending"}
                and not existing_anomalous
                and semantic_row.get("source") == "crop"
            ):
                continue

            reasons = [
                str(reason)
                for reason in semantic_row.get("crop_reasons", [])
                if str(reason)
            ]
            reasons.extend(sanity_reasons)
            if not reasons:
                reasons.append("page-audit-needs-crop")

            result.append(
                semantic.CropCandidate(
                    fingerprint=fingerprint,
                    publication=publication,
                    offer=offer,
                    page_fingerprint=str(semantic_row.get("page_fingerprint") or ""),
                    reasons=tuple(dict.fromkeys(reasons)),
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


def install() -> None:
    global _installed
    if _installed:
        return
    semantic._fact_schema = _strict_fact_schema
    semantic.page_fingerprint = _versioned_page_fingerprint
    semantic._page_schema = _strict_page_schema
    semantic._page_prompt = _strict_page_prompt
    semantic._validate_page_output = _strict_validate_page_output
    semantic._server_needs_crop = _strict_server_needs_crop
    semantic._crop_prompt = _strict_crop_prompt
    semantic._index_page_pricing_if_safe = _index_page_pricing_upgrading_legacy
    semantic.collect_crop_candidates = _crop_candidates_allowing_build58_reverification
    _installed = True

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
   re-read from the exact advert crop instead of being silently ignored;
6) a provider/page membership signal near an exact hotspot is recall evidence,
   never customer truth: if the page audit did not confirm a member-price badge,
   the exact crop is re-read before the signal can be dismissed.
"""

import hashlib
import json
import re

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_config, load_store, offer_fingerprint


SEMANTIC_AUDIT_CONTRACT_VERSION = "member-price-sanity-v2"
_MEMBER_COVERAGE_SIGNAL = "member-price-context-nearby-v3"

_installed = False
_original_fact_schema = semantic._fact_schema
_original_validate_page_output = semantic._validate_page_output
_original_index_page_pricing_if_safe = semantic._index_page_pricing_if_safe
_original_page_schema = semantic._page_schema
_original_page_prompt = semantic._page_prompt
_original_page_instructions = semantic._page_instructions
_original_page_context = semantic._page_context
_original_page_fingerprint = semantic.page_fingerprint
_original_server_needs_crop = semantic._server_needs_crop
_original_crop_prompt = semantic._crop_prompt
_original_crop_instructions = semantic._crop_instructions
_original_crop_context = semantic._crop_context

# OpenAI prompt caching starts at a 1,024-token prefix. This stable field
# contract deliberately makes the safety prefix self-contained and cacheable;
# all retailer/publication/offer facts still arrive later in the user message.
_STATIC_FACT_FIELD_CONTRACT = (
    "\n\nSTABLE OUTPUT FIELD CONTRACT: Treat each output field as an independent visual "
    "claim about the exact target advert. visible says whether the target advert can be "
    "located on the supplied image; it is not a confidence shortcut. product_name and brand "
    "must reflect readable pack or campaign text and must stay null when the identity is not "
    "safe. ordinary_price and member_price are numeric customer prices in Danish kroner, "
    "without currency symbols. Do not calculate either price from percentages, unit prices, "
    "multi-buy arithmetic or a provider value. ordinary_price is only the campaign amount "
    "available to a customer without joining a club, using an app membership or activating a "
    "member benefit. member_price is only an amount whose visual label, badge or layout binds "
    "it to a membership programme for this same target. If only one headline amount is visible "
    "and its role is ambiguous, do not place the same guess in both price fields. "
    "membership_price_visible describes the presence of a target-specific member-price visual "
    "treatment, independently of whether its amount can be read. member_program is the printed "
    "programme or club name and member_app is the printed app name; never invent a brand suffix "
    "from retailer knowledge. requires_activation is false for ordinary membership access and "
    "true only for an explicit activate, clip, choose or coupon action. before_price is a crossed "
    "out, comparison or normal-before amount and cannot substitute for ordinary_price unless "
    "the advert explicitly presents it as the current non-member campaign price. unit_price "
    "preserves the complete readable comparison such as kr/kg, kr/l, kr/100 g, kr/100 ml or "
    "kr/stk. package_size preserves weight, volume and pack-count metadata. Never derive a "
    "headline price from unit_price and package_size. multiple_products describes a campaign "
    "containing more than one concrete product or named choice, not merely several packages of "
    "one item. variants contains only concrete, visibly named same-campaign choices; exclude "
    "sizes, quantities, generic assortment wording, serving suggestions and neighbouring packs. "
    "identity_confidence measures target-to-advert association, pricing_confidence measures the "
    "visual role assignment of every returned price, and variant_confidence measures whether the "
    "named choice set is complete and correctly scoped. Confidence is evidence quality, not a "
    "permission to infer. needs_crop_verification must be true when smaller text, overlapping "
    "hotspots, unclear labels, incomplete member-price amounts, suspicious unit-price equality, "
    "or conflict with provider facts prevents a safe result. Null and empty outputs are valid and "
    "preferred whenever visual evidence is insufficient. Never use general retailer knowledge, "
    "earlier campaigns, filename text, URL parameters, adjacent offers, page banners unrelated to "
    "the target, or assumptions about typical club discounts as evidence."
)

_UNIT_TOKEN = r"(?P<unit>kg|kilo|l(?:iter)?|100\s*g|100\s*ml|stk\.?|styk(?:ker)?)"
_UNIT_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?<!\d)(?P<price>\d{{1,4}}(?:[,.]\d{{1,2}})?)\s*kr\.?\s*(?:/|pr\.?)\s*"
        rf"{_UNIT_TOKEN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bpr\.?\s*{_UNIT_TOKEN}\b"
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


def _normalize_unit(value: object) -> str:
    unit = re.sub(r"\s+", "", str(value or "").casefold().replace(".", ""))
    if unit in {"kg", "kilo"}:
        return "kg"
    if unit in {"l", "liter"}:
        return "l"
    if unit == "100g":
        return "100g"
    if unit == "100ml":
        return "100ml"
    if unit in {"stk", "styk", "stykker"}:
        return "stk"
    return ""


def _package_matches_unit_basis(package_size: object, unit: str) -> bool:
    if not isinstance(package_size, str) or not package_size.strip():
        return False
    package = package_size.casefold().replace(",", ".")
    patterns = {
        "kg": (
            r"(?:^|[^\d])1(?:\.0+)?\s*kg\b",
            r"(?:^|[^\d])1000\s*g\b",
        ),
        "l": (
            r"(?:^|[^\d])1(?:\.0+)?\s*(?:l|liter)\b",
            r"(?:^|[^\d])1000\s*ml\b",
        ),
        "100g": (r"(?:^|[^\d])100\s*g\b",),
        "100ml": (r"(?:^|[^\d])100\s*ml\b",),
        "stk": (r"(?:^|[^\d])1\s*(?:stk\.?|styk(?:ke|ker)?)\b",),
    }
    return any(re.search(pattern, package, re.IGNORECASE) for pattern in patterns.get(unit, ()))


def _unit_price_collision_is_package_equivalent(facts: dict, price) -> bool:
    """Allow equal headline/unit values when the package exactly equals the unit basis.

    12 kr for a 1 kg yoghurt legitimately has a unit price of 12 kr/kg. The same
    numeric equality for a 500 g pack is still suspicious and must fail closed.
    """
    unit_price = facts.get("unit_price")
    if not isinstance(unit_price, str) or not unit_price.strip():
        return False

    matching_units: list[str] = []
    for pattern in _UNIT_PRICE_PATTERNS:
        for match in pattern.finditer(unit_price):
            try:
                number = float(match.group("price").replace(",", "."))
            except (AttributeError, ValueError):
                continue
            if _same_numeric_price(price, number):
                unit = _normalize_unit(match.groupdict().get("unit"))
                if unit:
                    matching_units.append(unit)

    if not matching_units:
        return False
    return all(
        _package_matches_unit_basis(facts.get("package_size"), unit)
        for unit in matching_units
    )


def _pricing_sanity_reasons(offer, facts) -> tuple[str, ...]:
    """Return generic reasons why visual price roles are not safe yet.

    These checks never invent a replacement price. They only decide whether a
    sharper visual crop is required. This keeps genuinely unusual promotions
    possible while preventing a kg/stk comparison price, an unresolved badge or
    an unconfirmed provider/page membership hint from becoming customer-visible
    truth just because confidence is high.
    """
    if not isinstance(facts, dict) or not (facts.get("visible") or facts.get("same_offer")):
        return ()

    reasons: list[str] = []
    member = facts.get("member_price")
    ordinary = facts.get("ordinary_price")
    member_visible = facts.get("membership_price_visible") is True
    member_program = str(facts.get("member_program") or "").strip()
    member_app = str(facts.get("member_app") or "").strip()
    quality_signals = {
        str(value)
        for value in (getattr(offer, "quality_signals", None) or [])
        if str(value)
    }

    if _MEMBER_COVERAGE_SIGNAL in quality_signals and not member_visible:
        reasons.append("page-audit-provider-member-context-unresolved")

    if ordinary is None and _same_numeric_price(getattr(offer, "price", None), member):
        reasons.append("page-audit-primary-price-role-ambiguous")

    if member is None and member_visible:
        reasons.append("page-audit-visible-member-price-missing-value")
    elif member is None and (member_program or member_app):
        reasons.append("page-audit-member-program-without-price")

    unit_values = _unit_price_values(facts.get("unit_price"))
    if (
        any(_same_numeric_price(member, value) for value in unit_values)
        and not _unit_price_collision_is_package_equivalent(facts, member)
    ):
        reasons.append("page-audit-member-price-is-unit-price")
    if (
        any(_same_numeric_price(ordinary, value) for value in unit_values)
        and not _unit_price_collision_is_package_equivalent(facts, ordinary)
    ):
        reasons.append("page-audit-ordinary-price-is-unit-price")

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


def mandatory_pricing_crop_resolved(offer, facts, config: dict | None = None) -> bool:
    """Return whether an exact crop is final enough to leave the paid queue.

    Page-level recall hints may intentionally force one exact crop. Once that
    crop has confidently confirmed either a real price pair or the absence of a
    membership price on the exact advert, the persistent provider/page hint must
    not force the same paid crop again. Hard price-role contradictions still
    fail closed.
    """
    if not isinstance(facts, dict) or not (facts.get("visible") or facts.get("same_offer")):
        return False

    config = config or load_config()
    threshold = float(config.get("min_apply_confidence", 0.96))
    if float(facts.get("pricing_confidence") or 0) < threshold:
        return False

    ordinary = facts.get("ordinary_price")
    member = facts.get("member_price")
    if ordinary is None and member is None:
        return False

    for value in (ordinary, member):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
            return False

    if member is not None and ordinary is None:
        return False
    if member is not None and ordinary is not None and float(member) >= float(ordinary):
        return False

    member_visible = facts.get("membership_price_visible") is True
    member_program = str(facts.get("member_program") or "").strip()
    member_app = str(facts.get("member_app") or "").strip()
    if member is not None and not member_visible:
        return False
    if member is None and member_visible:
        return False
    if member is None and (member_program or member_app):
        return False

    unit_values = _unit_price_values(facts.get("unit_price"))
    if (
        any(_same_numeric_price(member, value) for value in unit_values)
        and not _unit_price_collision_is_package_equivalent(facts, member)
    ):
        return False
    if (
        any(_same_numeric_price(ordinary, value) for value in unit_values)
        and not _unit_price_collision_is_package_equivalent(facts, ordinary)
    ):
        return False

    return True


def _primary_member_role_ambiguous(offer, facts) -> bool:
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


def _strict_page_instructions():
    return _original_page_instructions() + (
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
        "stay in unit_price and must not be copied into ordinary_price or member_price merely because "
        "the numbers match. Numeric equality is legitimate only when the package itself exactly "
        "equals that unit basis, such as 1 kg at 12 kr also being 12 kr/kg. If a proposed member "
        "price is dramatically lower than the ordinary/provider price, re-check whether it is "
        "actually a unit price and request crop verification whenever the role is not visually "
        "unambiguous."
    ) + _STATIC_FACT_FIELD_CONTRACT


def _strict_page_prompt(candidate):
    return _strict_page_instructions() + "\n\n" + json.dumps(
        _original_page_context(candidate), ensure_ascii=False, separators=(",", ":")
    )


def _strict_crop_instructions():
    return _original_crop_instructions() + (
        "\n\nIMPORTANT TARGETED MEMBER-PRICE CONTRACT: Re-read this exact crop from scratch. "
        "Set membership_price_visible=true only when the target advert itself visibly has a "
        "membership/app/club/plus price treatment. Inspect every headline price and bind each to "
        "its visible role label/layout. provider_price is untyped and must never be copied into "
        "member_price merely because Plus/app/member text is visible. kg/l/100g/100ml/stk prices "
        "belong in unit_price and must not be mistaken for headline prices; numeric equality can be "
        "legitimate when the package exactly equals the unit basis, for example 1 kg at 12 kr also "
        "being 12 kr/kg. Examples such as 'Pr. stk. max. 1,98' are NOT member prices unless the "
        "advert explicitly binds that amount to membership. If page_audit_facts proposed a member "
        "price that equals an unrelated unit/comparison price, or a visible member badge had no "
        "amount, that result is specifically unresolved. When two customer prices are visible, "
        "return the non-member campaign price as ordinary_price and the explicitly membership-tied "
        "campaign price as member_price. If the role still cannot be read safely, return null and "
        "lower pricing_confidence rather than guessing."
    ) + _STATIC_FACT_FIELD_CONTRACT + (
        "\n\nSTABLE CROP READING ORDER: First locate the target pack, product name and hotspot "
        "boundary. Then enumerate every price-like number inside that boundary together with its "
        "nearest visible label, typography, badge, strike-through and unit suffix. Classify each "
        "number only after that evidence is paired: current non-member campaign price, explicit "
        "member campaign price, before/reference price, unit/comparison price, deposit, quantity "
        "or unrelated legal limit. Finally cross-check product identity, package size and named "
        "variants before assigning confidence. A large font alone does not define a price role, "
        "and spatial proximity alone does not bind a member badge to a neighbouring number. If "
        "the crop boundary still includes multiple adverts, use the named target and pack imagery "
        "to scope the answer and request further verification instead of resolving ambiguity by "
        "guessing. This reading order is mandatory even when provider facts look plausible."
    )


def _strict_crop_prompt(candidate):
    return _strict_crop_instructions() + "\n\n" + json.dumps(
        _original_crop_context(candidate), ensure_ascii=False, separators=(",", ":")
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
            existing_semantic_facts = (
                existing.get("semantic_facts") if isinstance(existing, dict) else None
            )
            existing_facts = (
                existing_semantic_facts
                if isinstance(existing_semantic_facts, dict)
                else existing.get("facts") if isinstance(existing, dict) else None
            )

            if isinstance(existing, dict) and existing.get("analysis_level") == "crop":
                status = str(existing.get("status") or "")
                reusable_failed_crop = (
                    status == "failed"
                    and str(existing.get("error") or "") == "completed"
                    and isinstance(existing_facts, dict)
                )
                if status == "completed" or reusable_failed_crop:
                    if not isinstance(existing_facts, dict) and not sanity_reasons:
                        continue
                    if mandatory_pricing_crop_resolved(offer, existing_facts):
                        continue

            existing_anomalous = bool(_pricing_sanity_reasons(offer, existing_facts))
            if (
                isinstance(existing, dict)
                and existing.get("analysis_level") == "crop"
                and existing.get("status") in {"no-change", "pending"}
                and (not isinstance(existing_facts, dict) or not existing_anomalous)
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

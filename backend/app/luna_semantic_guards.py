from __future__ import annotations

"""Safety guards around Kurv's semantic flyer-audit engine.

The invariants are deliberately explicit and independently testable:
1) a page is never accepted as fully audited when Luna omitted a target hotspot;
2) an old pre-Build58 Luna result can never block a crop explicitly requested by
   the newer page audit;
3) a safe page result may upgrade legacy pricing while a targeted crop remains
   the strongest cached visual result;
4) an AI result that merely relabels the provider primary price as a member
   price without resolving an ordinary price is never considered price-safe.
"""

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_store, offer_fingerprint


_installed = False
_original_validate_page_output = semantic._validate_page_output
_original_index_page_pricing_if_safe = semantic._index_page_pricing_if_safe
_original_page_schema = semantic._page_schema
_original_page_prompt = semantic._page_prompt
_original_server_needs_crop = semantic._server_needs_crop
_original_crop_prompt = semantic._crop_prompt


def _same_numeric_price(left, right, *, tolerance: float = 0.005) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return abs(float(left) - float(right)) <= tolerance


def _primary_member_role_ambiguous(offer, facts) -> bool:
    """Detect the unsafe role shape found by the live Seafoodmix regression.

    Provider `price` is not role-labelled. If the page model repeats that exact
    value as `member_price` but cannot identify any ordinary price, it has not
    actually resolved whether the provider value is ordinary or member. That is
    precisely the case where a sharp advert crop adds information.
    """
    if not isinstance(facts, dict) or not facts.get("visible"):
        return False
    if facts.get("ordinary_price") is not None:
        return False
    return _same_numeric_price(getattr(offer, "price", None), facts.get("member_price"))


def _strict_page_schema(candidate):
    schema = _original_page_schema(candidate)
    count = max(1, len(candidate.offers))
    offers_schema = schema["properties"]["offers"]
    # Structured output itself now requires full page coverage, rather than
    # relying only on post-response validation.
    offers_schema["minItems"] = count
    offers_schema["maxItems"] = count
    return schema


def _strict_page_prompt(candidate):
    return _original_page_prompt(candidate) + (
        "\n\nIMPORTANT COVERAGE CONTRACT: Return exactly one result for EVERY target offer_id "
        "listed in the context, no more and no fewer. If a target cannot be safely associated "
        "with a visible advert, still return its exact offer_id with visible=false, null/empty "
        "facts, low confidences, and needs_crop_verification=true. Never omit a target."
        "\n\nIMPORTANT PRICE-ROLE CONTRACT: provider_price is an untyped source value, not "
        "evidence that the value is ordinary_price or member_price. Assign ordinary/member roles "
        "only from visible labels and layout in the target advert. If the only proposed member "
        "price equals provider_price and no ordinary price can be identified, set "
        "needs_crop_verification=true instead of declaring the role resolved."
    )


def _strict_crop_prompt(candidate):
    return _original_crop_prompt(candidate) + (
        "\n\nIMPORTANT TARGETED PRICE-ROLE CONTRACT: The provider_price is untyped and must "
        "never be copied into member_price merely because Plus/app/member text is visible. Inspect "
        "every headline price inside this exact crop and bind each price to its visible role label "
        "such as PLUS PRIS, + PRIS, MEDLEMSPRIS, non-member/ordinary wording, or the corresponding "
        "layout. If page_audit_facts said member_price equals provider_price while ordinary_price "
        "was null, that page result is specifically unresolved; re-read the crop from scratch. "
        "When two customer prices are visible, return the higher non-member campaign price as "
        "ordinary_price and the explicitly membership-tied price as member_price. If the role still "
        "cannot be read safely, return null and lower pricing_confidence rather than guessing."
    )


def _strict_validate_page_output(value, allowed_ids):
    rows = _original_validate_page_output(value, allowed_ids)
    if rows is None:
        return None
    returned_ids = {row.get("offer_id") for row in rows}
    # A partial page response is a failed audit, not a completed page. The page
    # can then be retried according to page_audit_max_failures instead of
    # silently leaving an unaudited blind spot.
    if returned_ids != set(allowed_ids):
        return None
    return rows


def _strict_server_needs_crop(offer, facts, threshold):
    if _primary_member_role_ambiguous(offer, facts):
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
        # A Build56/57 record has no analysis_level. If the newer semantic audit
        # has a safe page result, allow it to become canonical. A targeted crop
        # is never removed by this upgrade path.
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
            if not isinstance(semantic_row, dict) or semantic_row.get("source") != "page-audit":
                continue

            facts = semantic_row.get("facts")
            role_ambiguous = _primary_member_role_ambiguous(offer, facts)
            if not semantic_row.get("needs_crop") and not role_ambiguous:
                continue

            fingerprint = offer_fingerprint(offer)
            existing = records.get(fingerprint)
            # Only a real targeted crop may satisfy a newer crop request. Legacy
            # records and page-audit pricing records must not suppress visual
            # reverification. This also lets already-persisted `needs_crop=false`
            # rows from the old price-role contract be caught by role_ambiguous.
            if (
                isinstance(existing, dict)
                and existing.get("analysis_level") == "crop"
                and existing.get("status") in {"completed", "no-change", "pending"}
            ):
                continue

            reasons = [
                str(reason)
                for reason in semantic_row.get("crop_reasons", [])
                if str(reason)
            ]
            if role_ambiguous:
                reasons.append("page-audit-primary-price-role-ambiguous")
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
    semantic._page_schema = _strict_page_schema
    semantic._page_prompt = _strict_page_prompt
    semantic._validate_page_output = _strict_validate_page_output
    semantic._server_needs_crop = _strict_server_needs_crop
    semantic._crop_prompt = _strict_crop_prompt
    semantic._index_page_pricing_if_safe = _index_page_pricing_upgrading_legacy
    semantic.collect_crop_candidates = _crop_candidates_allowing_build58_reverification
    _installed = True

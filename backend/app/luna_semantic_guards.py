from __future__ import annotations

"""Safety guards around the Build 58 semantic-audit engine.

The invariants are deliberately explicit and independently testable:
1) a page is never accepted as fully audited when Luna omitted a target hotspot;
2) an old pre-Build58 Luna result can never block a crop explicitly requested by
   the new page audit;
3) a new safe Build58 page result may upgrade a legacy v1 record, while a real
   Build58 targeted crop remains the strongest cached result.
"""

from . import luna_semantic_audit as semantic
from .luna_enrichment import load_store, offer_fingerprint


_installed = False
_original_validate_page_output = semantic._validate_page_output
_original_index_page_pricing_if_safe = semantic._index_page_pricing_if_safe
_original_page_schema = semantic._page_schema
_original_page_prompt = semantic._page_prompt


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
        # A Build56/57 record has no analysis_level. If Build58 has a safe page
        # result, allow the newer semantic audit to become the canonical cached
        # pricing result. A Build58 crop (analysis_level='crop') is never removed.
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
            if not semantic_row.get("needs_crop"):
                continue

            fingerprint = offer_fingerprint(offer)
            existing = records.get(fingerprint)
            # Only a Build58 targeted crop may satisfy a Build58 crop request.
            # Legacy v1 records (analysis_level missing/None) and page-audit
            # pricing records must not suppress a new visual verification.
            if (
                isinstance(existing, dict)
                and existing.get("analysis_level") == "crop"
                and existing.get("status") in {"completed", "no-change", "pending"}
            ):
                continue

            reasons = tuple(
                str(reason)
                for reason in semantic_row.get("crop_reasons", [])
                if str(reason)
            ) or ("page-audit-needs-crop",)
            result.append(
                semantic.CropCandidate(
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


def install() -> None:
    global _installed
    if _installed:
        return
    semantic._page_schema = _strict_page_schema
    semantic._page_prompt = _strict_page_prompt
    semantic._validate_page_output = _strict_validate_page_output
    semantic._index_page_pricing_if_safe = _index_page_pricing_upgrading_legacy
    semantic.collect_crop_candidates = _crop_candidates_allowing_build58_reverification
    _installed = True

from __future__ import annotations

"""Offer-level validity contract for Kurv's existing Luna page audit.

This module adds no extra OpenAI request type. It extends the page audit that
already runs, and selectively invalidates old page-audit cache entries only
when deterministic source text contains an explicit date-range marker.
"""

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Iterable

from .meny_flyer import Offer, Publication


VALIDITY_CONTRACT_VERSION = "offer-validity-v1"
_DATE_FORMAT = "%d.%m.%Y"
_DATE_VALUE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_MONTH = (
    r"(?:jan(?:uar)?|feb(?:ruar)?|mar(?:ts)?|apr(?:il)?|maj|jun(?:i)?|"
    r"jul(?:i)?|aug(?:ust)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_TOKEN = rf"(?:\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{2,4}})?|\d{{1,2}}\.?\s*{_MONTH})"
_PAGE_VALIDITY_MARKER_RE = re.compile(
    rf"\b(?:fra|g[æa]lder|gyldig(?:t|hed)?)\b.{{0,70}}{_DATE_TOKEN}"
    rf".{{0,120}}\b(?:til\s+og\s+med|t\.?\s*o\.?\s*m\.?|til)\b.{{0,70}}{_DATE_TOKEN}",
    re.IGNORECASE | re.DOTALL,
)


def _page_text(publication: Publication, page_number: int) -> str:
    if 0 < page_number <= len(publication.page_texts):
        return str(publication.page_texts[page_number - 1] or "")
    return ""


def page_has_explicit_validity_marker(publication: Publication, page_number: int) -> bool:
    return bool(_PAGE_VALIDITY_MARKER_RE.search(_page_text(publication, page_number)))


def page_fingerprint(
    publication: Publication,
    page_number: int,
    offers: Iterable[Offer],
    *,
    base_fingerprint: str,
) -> str:
    """Invalidate old audits only for pages likely to contain a date range.

    New pages are audited normally regardless. This avoids a paid full-archive
    re-scan merely because the output schema learned one new field family.
    """

    del offers  # represented by the already-versioned base fingerprint
    if not page_has_explicit_validity_marker(publication, page_number):
        return base_fingerprint
    raw = f"{VALIDITY_CONTRACT_VERSION}|{base_fingerprint}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def extend_fact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(schema))
    properties = result.setdefault("properties", {})
    properties["offer_valid_from"] = {"type": ["string", "null"]}
    properties["offer_valid_until"] = {"type": ["string", "null"]}
    properties["validity_confidence"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    required = result.setdefault("required", [])
    for key in ("offer_valid_from", "offer_valid_until", "validity_confidence"):
        if key not in required:
            required.append(key)
    return result


def page_context(context: dict[str, Any], publication: Publication) -> dict[str, Any]:
    result = dict(context)
    result["publication_valid_from"] = publication.valid_from
    result["publication_valid_until"] = publication.valid_until
    return result


def page_instructions(existing: str) -> str:
    return existing + (
        "\n\nIMPORTANT OFFER-VALIDITY CONTRACT: For every target, inspect the page for an "
        "explicit campaign validity period that governs that exact target. This may be printed "
        "inside the advert or in a clearly page-wide banner such as 'Fra søndag d. 23. august "
        "til og med onsdag d. 26. august'. Return offer_valid_from and offer_valid_until in "
        "DD.MM.YYYY only when the date range is visually bound to the target or clearly governs "
        "all offers on that page. If the page prints day/month without a year, you may use the "
        "year from publication_valid_from/publication_valid_until only when that makes the year "
        "unambiguous. Do NOT merely copy the publication period into these fields. If no separate "
        "offer/page validity is visible, return both fields null and validity_confidence=0. "
        "validity_confidence measures confidence in the date-to-target association, not OCR "
        "legibility alone. If a potentially relevant date is visible but cannot be safely bound "
        "or read, lower validity_confidence and set needs_crop_verification=true rather than "
        "guessing."
    )


def _normalized_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("validity date must be a string or null")
    text = value.strip()
    if not _DATE_VALUE_RE.fullmatch(text):
        raise ValueError("validity date must use DD.MM.YYYY")
    parsed = datetime.strptime(text, _DATE_FORMAT).date()
    return parsed.strftime(_DATE_FORMAT)


def validate_page_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if rows is None:
        return None
    result: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        try:
            valid_from = _normalized_date(row.get("offer_valid_from"))
            valid_until = _normalized_date(row.get("offer_valid_until"))
        except ValueError:
            return None

        confidence = row.get("validity_confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            return None
        if valid_from and valid_until:
            start = datetime.strptime(valid_from, _DATE_FORMAT).date()
            end = datetime.strptime(valid_until, _DATE_FORMAT).date()
            if start > end:
                return None
        row["offer_valid_from"] = valid_from
        row["offer_valid_until"] = valid_until
        row["validity_confidence"] = float(confidence)
        result.append(row)
    return result


def safe_offer_validity(
    facts: dict[str, Any],
    threshold: float,
) -> tuple[str | None, str | None]:
    confidence = facts.get("validity_confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or float(confidence) < threshold
    ):
        return None, None
    try:
        valid_from = _normalized_date(facts.get("offer_valid_from"))
        valid_until = _normalized_date(facts.get("offer_valid_until"))
    except ValueError:
        return None, None
    if valid_from and valid_until:
        start = datetime.strptime(valid_from, _DATE_FORMAT).date()
        end = datetime.strptime(valid_until, _DATE_FORMAT).date()
        if start > end:
            return None, None
    return valid_from, valid_until


def starts_in_future(value: str | None, *, today: date | None = None) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.strptime(value, _DATE_FORMAT).date()
    except ValueError:
        return False
    return parsed > (today or date.today())


__all__ = [
    "VALIDITY_CONTRACT_VERSION",
    "extend_fact_schema",
    "page_context",
    "page_fingerprint",
    "page_has_explicit_validity_marker",
    "page_instructions",
    "safe_offer_validity",
    "starts_in_future",
    "validate_page_rows",
]

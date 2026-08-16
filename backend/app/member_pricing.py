"""Stable public surface for Kurv's generic member-pricing classifier."""

from __future__ import annotations

import re
from dataclasses import replace

from .member_pricing_v3 import (
    MemberPricing,
    detect_member_pricing as _detect_member_pricing_v3,
    has_membership_signal,
)

_EXPLICIT_ORDINARY_RE = re.compile(
    r"\b(?:pris\s+ikke[-\s]?medlem|ikke[-\s]?medlems?pris|normal[-\s]?pris|almindelig\s+pris)\b"
    r"[^\d]{0,18}(?P<price>\d{1,4}(?:[,.]\d{2}|\s*(?:[,.]|:)\s*[-–])?|\d{1,4}\s*kr\.?)",
    re.IGNORECASE,
)
_EXPLICIT_ACTIVATION_RE = re.compile(
    r"\b(?:aktiv[ée]r(?:e|es|et)?|aktiver(?:e|es|et)?|klip(?:pe|pes|pet)?)\b",
    re.IGNORECASE,
)
_PROGRAMS: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"\blidl\s*plus\b", re.IGNORECASE), "Lidl Plus", "Lidl Plus"),
    (re.compile(r"\bnetto\s*(?:\+|plus)\b", re.IGNORECASE), "Netto+", "Netto+"),
    (re.compile(r"\b(?:føtex|foetex)\s*plus\b", re.IGNORECASE), "føtex Plus", "føtex Plus"),
    (re.compile(r"\bbilka\s*plus\b", re.IGNORECASE), "Bilka Plus", "Bilka Plus"),
    (re.compile(r"\bcoop\s*(?:medlems?(?:pris)?|plus|app)\b", re.IGNORECASE), "Coop medlemspris", "Coop-appen"),
    (re.compile(r"\bspar\s*sammen\b", re.IGNORECASE), "SPAR SAMMEN medlemspris", "SPAR SAMMEN"),
)


def _number(value: str) -> float | None:
    raw = value.casefold().replace("kr", "").strip()
    raw = re.sub(r"\s*(?:[,.]|:)\s*[-–]$", ".00", raw).replace(",", ".")
    try:
        number = float(raw.rstrip(".").strip())
    except ValueError:
        return None
    return round(number, 2) if 0 < number <= 10_000 else None


def detect_member_pricing(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None = None,
) -> MemberPricing | None:
    result = _detect_member_pricing_v3(
        retailer=retailer,
        price=price,
        normal_price=normal_price,
        text=text,
        unit_price=unit_price,
    )
    if result is None:
        return None

    # An explicitly labelled non-member/normal price is stronger evidence than
    # a provider's generic primary/reference field. This is what separates a
    # 13 kr non-member product price from a 200 kr Coop membership fee.
    ordinary = result.ordinary_price
    if match := _EXPLICIT_ORDINARY_RE.search(text or ""):
        explicit = _number(match.group("price"))
        if explicit is not None and explicit > result.member_price + 0.005:
            ordinary = explicit

    label, app_name = result.label, result.app_name
    for pattern, program_label, program_app in _PROGRAMS:
        if pattern.search(text or ""):
            label, app_name = program_label, program_app
            break

    # A membership/app/club requirement is not the same as having to activate
    # a coupon. The in-store reminder is enabled only by explicit activation or
    # clipping language in the advert.
    activation = bool(_EXPLICIT_ACTIVATION_RE.search(text or ""))
    return replace(
        result,
        ordinary_price=ordinary,
        label=label,
        app_name=app_name,
        requires_activation=activation,
    )


__all__ = ["MemberPricing", "detect_member_pricing", "has_membership_signal"]

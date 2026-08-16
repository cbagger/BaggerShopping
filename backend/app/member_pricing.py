"""Stable public surface for Kurv's generic member-pricing classifier."""

from __future__ import annotations

import re
from dataclasses import replace

from .member_pricing_v3 import (
    MemberPricing,
    detect_member_pricing as _detect_member_pricing_v3,
    has_membership_signal,
)

_PRICE_TOKEN = r"(?:\d{1,4}[,.]\d{2}|\d{1,4}\s*(?:[,.]|:)\s*[-–]|\d{1,4}\s*kr\.?|\d{1,4})"
_EXPLICIT_ORDINARY_RE = re.compile(
    r"\b(?:pris\s+ikke[-\s]?medlem|ikke[-\s]?medlems?pris|normal[-\s]?pris|almindelig\s+pris)\b"
    rf"[^\d]{{0,18}}(?P<price>{_PRICE_TOKEN})",
    re.IGNORECASE,
)
_EXPLICIT_MEMBER_RE = re.compile(
    r"\b(?:medlems?[-\s_]?pris|kundeklub[-\s_]?pris|klub[-\s_]?pris|club[-\s_]?price|plus[-\s_]?pris|app[-\s_]?pris|member[-\s_]?price)\b"
    rf"[^\d]{{0,24}}(?P<price>{_PRICE_TOKEN})",
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


def _program(text: str, retailer: str) -> tuple[str, str | None]:
    for pattern, label, app_name in _PROGRAMS:
        if pattern.search(text):
            return label, app_name

    retailer_key = retailer.casefold().strip()
    if re.search(r"\bapp[-\s_]?pris\b", text, re.IGNORECASE):
        return "App-pris", None
    if re.search(r"\bplus[-\s_]?pris\b", text, re.IGNORECASE):
        known = {
            "lidl": ("Lidl Plus", "Lidl Plus"),
            "netto": ("Netto+", "Netto+"),
            "føtex": ("føtex Plus", "føtex Plus"),
            "foetex": ("føtex Plus", "føtex Plus"),
            "bilka": ("Bilka Plus", "Bilka Plus"),
        }
        return known.get(retailer_key, ("Pluspris", None))
    if re.search(r"\b(?:kundeklub|klub)[-\s_]?pris\b", text, re.IGNORECASE):
        return "Kundeklubpris", None
    if retailer_key == "meny" and re.search(r"\bmedlems?[-\s_]?pris\b", text, re.IGNORECASE):
        return "MENY medlemspris", "MENY-appen"
    return "Medlemspris", None


def _explicit_fallback(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
) -> MemberPricing | None:
    # Tagged page context is deliberately only review evidence. A neighbouring
    # advert must never be rescued into a badge by this fallback.
    if "[kurv-page-context]" in text:
        return None
    member_match = _EXPLICIT_MEMBER_RE.search(text)
    if member_match is None:
        return None
    member_price = _number(member_match.group("price"))
    if member_price is None:
        return None

    ordinary: float | None = None
    if ordinary_match := _EXPLICIT_ORDINARY_RE.search(text):
        candidate = _number(ordinary_match.group("price"))
        if candidate is not None and candidate > member_price + 0.005:
            ordinary = candidate
    if ordinary is None and price is not None and price > member_price + 0.005:
        # Only trust the provider value when it is plausible as a product price.
        if not (member_price >= 5 and price > member_price * 4 and price - member_price > 60):
            ordinary = round(price, 2)
    if ordinary is None and normal_price is not None and normal_price > member_price + 0.005:
        if not (member_price >= 5 and normal_price > member_price * 4 and normal_price - member_price > 60):
            ordinary = round(normal_price, 2)

    label, app_name = _program(text, retailer)
    return MemberPricing(
        ordinary_price=ordinary,
        member_price=member_price,
        label=label,
        app_name=app_name,
        requires_activation=bool(_EXPLICIT_ACTIVATION_RE.search(text)),
        source="structured-explicit-role-fallback",
        primary_price_was_member=(price is not None and abs(price - member_price) < 0.005),
        confidence=0.99,
    )


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
        return _explicit_fallback(
            retailer=retailer,
            price=price,
            normal_price=normal_price,
            text=text or "",
        )

    # A cached high-confidence Luna decision has already passed the confidence
    # gate. Do not reinterpret it with the weak provider text that triggered AI.
    if result.source == "luna-verified":
        return result

    ordinary = result.ordinary_price
    if match := _EXPLICIT_ORDINARY_RE.search(text or ""):
        explicit = _number(match.group("price"))
        if explicit is not None and explicit > result.member_price + 0.005:
            ordinary = explicit

    label, app_name = result.label, result.app_name
    normalized_label, normalized_app = _program(text or "", retailer)
    if normalized_label != "Medlemspris" or normalized_app is not None:
        label, app_name = normalized_label, normalized_app

    # Membership/app access is not the same as explicit coupon activation.
    activation = bool(_EXPLICIT_ACTIVATION_RE.search(text or ""))
    return replace(
        result,
        ordinary_price=ordinary,
        label=label,
        app_name=app_name,
        requires_activation=activation,
    )


__all__ = ["MemberPricing", "detect_member_pricing", "has_membership_signal"]

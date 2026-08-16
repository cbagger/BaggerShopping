from __future__ import annotations

import re
from dataclasses import dataclass


_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmedlems?[-\s]?pris\b", re.IGNORECASE),
    re.compile(r"\bkundeklub[-\s]?pris\b", re.IGNORECASE),
    re.compile(r"\bklub[-\s]?pris\b", re.IGNORECASE),
    re.compile(r"\bclub[-\s]?price\b", re.IGNORECASE),
    re.compile(r"\bplus[-\s]?pris\b", re.IGNORECASE),
    re.compile(r"\bapp[-\s]?pris\b", re.IGNORECASE),
)

_PROGRAM_PATTERNS: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"\blidl\s*plus\b", re.IGNORECASE), "Lidl Plus", "Lidl Plus"),
    (re.compile(r"\bnetto\s*(?:\+|plus)\b", re.IGNORECASE), "Netto+", "Netto+"),
    (re.compile(r"\b(?:føtex|foetex)\s*plus\b", re.IGNORECASE), "føtex Plus", "føtex Plus"),
    (re.compile(r"\bbilka\s*plus\b", re.IGNORECASE), "Bilka Plus", "Bilka Plus"),
    (re.compile(r"\bcoop\s*(?:medlem|plus|app)\b", re.IGNORECASE), "Coop medlemspris", "Coop"),
)

# Deliberately require either currency syntax or a two-decimal price. This
# keeps quantities such as 200 g and 24 x 33 cl out of member-price matching.
_PRICE_RE = re.compile(
    r"(?<!\d)(?:"
    r"(?P<decimal>\d{1,4}[,.]\d{2})"
    r"|(?P<dash>\d{1,4}[,.]-)"
    r"|(?P<space>\d{1,3}\s+\d{2})(?=\s*(?:kr\.?|$|[^\d]))"
    r"|(?P<whole>\d{1,4})\s*kr\.?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MemberPricing:
    ordinary_price: float | None
    member_price: float
    label: str
    app_name: str | None
    requires_activation: bool
    source: str
    primary_price_was_member: bool


def _price_value(match: re.Match[str]) -> float | None:
    raw = match.group(0).casefold().replace("kr", "").replace(".", ".").strip()
    raw = raw.replace(",", ".").replace(".-", ".00").replace(".-", ".00")
    raw = raw.replace("-", "0") if raw.endswith("-") else raw
    if match.group("space"):
        parts = match.group("space").split()
        raw = f"{parts[0]}.{parts[1]}"
    raw = raw.rstrip(".").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value > 10_000:
        return None
    return round(value, 2)


def _same_price(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) < 0.005


def _program(text: str, retailer: str) -> tuple[str | None, str | None, re.Match[str] | None]:
    for pattern, label, app_name in _PROGRAM_PATTERNS:
        if match := pattern.search(text):
            return label, app_name, match

    retailer_key = retailer.casefold().strip()
    if retailer_key == "meny" and re.search(r"\bmedlems?[-\s]?pris\b", text, re.IGNORECASE):
        return "MENY medlemspris", "MENY-appen", None
    return None, None, None


def _generic_label(text: str) -> str:
    if re.search(r"\bapp[-\s]?pris\b", text, re.IGNORECASE):
        return "App-pris"
    if re.search(r"\bplus[-\s]?pris\b", text, re.IGNORECASE):
        return "Pluspris"
    if re.search(r"\b(?:kundeklub|klub)[-\s]?pris\b", text, re.IGNORECASE):
        return "Kundeklubpris"
    return "Medlemspris"


def detect_member_pricing(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
) -> MemberPricing | None:
    """Classify a member/app/club price without using image recognition.

    The detector is deliberately text-first and retailer-agnostic. Known club
    names only normalize an explicitly observed programme name; they never make
    a retailer a member-price offer by themselves.
    """
    compact = " ".join((text or "").replace("\u00ad", "").split())
    if not compact:
        return None

    label, app_name, programme_match = _program(compact, retailer)
    marker_matches = [match for pattern in _MARKER_PATTERNS for match in pattern.finditer(compact)]
    if programme_match is not None:
        marker_matches.append(programme_match)
    if not marker_matches:
        return None

    price_matches: list[tuple[int, int, float]] = []
    for match in _PRICE_RE.finditer(compact):
        if (value := _price_value(match)) is not None:
            price_matches.append((match.start(), match.end(), value))

    member_price: float | None = None
    source = "member-marker"
    if price_matches:
        ranked: list[tuple[int, int, float]] = []
        for marker in marker_matches:
            for start, end, value in price_matches:
                if end < marker.start():
                    distance = marker.start() - end
                    after_penalty = 8
                elif start > marker.end():
                    distance = start - marker.end()
                    after_penalty = 0
                else:
                    distance = 0
                    after_penalty = 0
                if distance <= 90:
                    ranked.append((distance + after_penalty, start, value))
        if ranked:
            ranked.sort(key=lambda value: (value[0], value[1]))
            member_price = ranked[0][2]
            source = "member-marker-price"

    # Some provider feeds expose the marked club price as the primary structured
    # price while the text only contains the club marker. We may classify that
    # case only when a higher reference price is also present; otherwise there
    # is not enough evidence to relabel the primary price.
    if member_price is None:
        if price is None or normal_price is None or normal_price <= price:
            return None
        member_price = round(price, 2)
        source = "member-marker-structured-price"

    primary_is_member = _same_price(price, member_price)
    ordinary_price = price

    if primary_is_member:
        # If the flyer itself contains another plausible shelf price, prefer it
        # over a provider pre-price. This covers adverts such as 16 kr. / member
        # price 9,95 kr. even when the feed promotes 9,95 as its main price.
        other_prices = sorted({
            value for _, _, value in price_matches
            if not _same_price(value, member_price)
            and value > member_price
            and (normal_price is None or value <= normal_price + 0.005)
        })
        if other_prices:
            ordinary_price = other_prices[0]
        elif normal_price is not None and normal_price > member_price:
            ordinary_price = round(normal_price, 2)
        else:
            ordinary_price = None

    # A detected membership price must actually be preferential when an
    # ordinary price is known. Reject contradictory OCR rather than swapping
    # the two prices or presenting false precision.
    if ordinary_price is not None and member_price >= ordinary_price - 0.005:
        return None

    return MemberPricing(
        ordinary_price=ordinary_price,
        member_price=round(member_price, 2),
        label=label or _generic_label(compact),
        app_name=app_name,
        requires_activation=True,
        source=source,
        primary_price_was_member=primary_is_member,
    )

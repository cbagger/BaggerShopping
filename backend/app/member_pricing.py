from __future__ import annotations

import re
from dataclasses import dataclass


_EXPLICIT_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmedlems?[-\s_]?pris\b", re.IGNORECASE),
    re.compile(r"\bkundeklub[-\s_]?pris\b", re.IGNORECASE),
    re.compile(r"\bklub[-\s_]?pris\b", re.IGNORECASE),
    re.compile(r"\bclub[-\s_]?price\b", re.IGNORECASE),
    re.compile(r"\bplus[-\s_]?pris\b", re.IGNORECASE),
    re.compile(r"\bapp[-\s_]?pris\b", re.IGNORECASE),
    re.compile(r"\bmember[-\s_]?price\b", re.IGNORECASE),
)

_PROGRAM_PATTERNS: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"\blidl\s*plus\b", re.IGNORECASE), "Lidl Plus", "Lidl Plus"),
    (re.compile(r"\bnetto\s*(?:\+|plus\b)", re.IGNORECASE), "Netto+", "Netto+"),
    (re.compile(r"\b(?:føtex|foetex)\s*plus\b", re.IGNORECASE), "føtex Plus", "føtex Plus"),
    (re.compile(r"\bbilka\s*plus\b", re.IGNORECASE), "Bilka Plus", "Bilka Plus"),
    (re.compile(r"\bcoop\s*(?:medlem|plus|app)\b", re.IGNORECASE), "Coop medlemspris", "Coop"),
)

# Danish flyers use all of 9,95 / 9.95 / 9,- / 9.- / 9 95 / 9 kr.
# Whole numbers without a price suffix are intentionally not accepted because
# package sizes, limits and percentages are much more common than bare prices.
_PRICE_RE = re.compile(
    r"(?<!\d)(?:"
    r"(?P<decimal>\d{1,4}[,.]\d{2})"
    r"|(?P<dash>\d{1,4}\s*[,.]\s*[-–])"
    r"|(?P<space>\d{1,3}\s+\d{2})(?=\s*(?:kr\.?|$|[^\d]))"
    r"|(?P<whole>\d{1,4})\s*kr\.?)",
    re.IGNORECASE,
)

_UNIT_PRICE_RE = re.compile(
    r"(?:"
    r"\bpr\.?\s*(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml)\b"
    r"|\b(?:kg|kilo|liter|l)[-\s]?pris\b"
    r"|\bkr\.?\s*/\s*(?:kg|l)\b"
    r"|\b(?:kg|kilo)\s*max\.?\b"
    r")",
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


@dataclass(frozen=True)
class _PriceCandidate:
    start: int
    end: int
    value: float
    unit_price_context: bool


def _price_value(match: re.Match[str]) -> float | None:
    raw = match.group(0).casefold().replace("kr", "").strip()
    raw = re.sub(r"\s*[,.]\s*[-–]$", ".00", raw)
    raw = raw.replace(",", ".")
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


def _unit_price_context(text: str, start: int, end: int) -> bool:
    # Unit-price labels often sit immediately before or after the numeric value,
    # e.g. "Pr. kg max. 166,67". Keep the window deliberately small so a valid
    # shelf price in the same advert is not discarded because kg-pris appears
    # elsewhere in the copy.
    left = max(0, start - 42)
    right = min(len(text), end + 28)
    return _UNIT_PRICE_RE.search(text[left:right]) is not None


def _price_candidates(text: str) -> list[_PriceCandidate]:
    result: list[_PriceCandidate] = []
    for match in _PRICE_RE.finditer(text):
        value = _price_value(match)
        if value is None:
            continue
        result.append(
            _PriceCandidate(
                start=match.start(),
                end=match.end(),
                value=value,
                unit_price_context=_unit_price_context(text, match.start(), match.end()),
            )
        )
    return result


def _program(text: str, retailer: str) -> tuple[str | None, str | None, re.Match[str] | None]:
    for pattern, label, app_name in _PROGRAM_PATTERNS:
        if match := pattern.search(text):
            return label, app_name, match

    retailer_key = retailer.casefold().strip()
    plus_match = re.search(r"\bplus[-\s_]?pris\b", text, re.IGNORECASE)
    if plus_match is not None:
        plus_programs = {
            "lidl": ("Lidl Plus", "Lidl Plus"),
            "netto": ("Netto+", "Netto+"),
            "føtex": ("føtex Plus", "føtex Plus"),
            "foetex": ("føtex Plus", "føtex Plus"),
            "bilka": ("Bilka Plus", "Bilka Plus"),
        }
        if retailer_key in plus_programs:
            label, app_name = plus_programs[retailer_key]
            return label, app_name, plus_match

    member_match = re.search(r"\bmedlems?[-\s_]?pris\b", text, re.IGNORECASE)
    if retailer_key == "meny" and member_match is not None:
        return "MENY medlemspris", "MENY-appen", member_match
    return None, None, None


def _generic_label(text: str) -> str:
    if re.search(r"\bapp[-\s_]?pris\b", text, re.IGNORECASE):
        return "App-pris"
    if re.search(r"\bplus[-\s_]?pris\b", text, re.IGNORECASE):
        return "Pluspris"
    if re.search(r"\b(?:kundeklub|klub)[-\s_]?pris\b", text, re.IGNORECASE):
        return "Kundeklubpris"
    return "Medlemspris"


def _rank_member_price(
    markers: list[re.Match[str]],
    prices: list[_PriceCandidate],
    *,
    max_distance: int,
) -> float | None:
    ranked: list[tuple[int, int, float]] = []
    for marker in markers:
        for candidate in prices:
            if candidate.unit_price_context:
                continue
            if candidate.end < marker.start():
                distance = marker.start() - candidate.end
                direction_penalty = 14
            elif candidate.start > marker.end():
                distance = candidate.start - marker.end()
                direction_penalty = 0
            else:
                distance = 0
                direction_penalty = 0
            if distance <= max_distance:
                ranked.append((distance + direction_penalty, candidate.start, candidate.value))
    if not ranked:
        return None
    ranked.sort(key=lambda value: (value[0], value[1]))
    return ranked[0][2]


def _normal_price_is_plausible(
    normal_price: float | None,
    *,
    price: float | None,
    member_price: float,
    prices: list[_PriceCandidate],
) -> bool:
    if normal_price is None or normal_price <= member_price + 0.005:
        return False

    # If the same number is explicitly printed in unit-price context, it is not
    # a shelf/før-price. This is the exact failure mode seen with føtex where
    # 166,67 kr/kg was supplied as pre_price beside 29/25 kr.
    if any(
        candidate.unit_price_context and _same_price(candidate.value, normal_price)
        for candidate in prices
    ):
        return False

    reference = price if price is not None and price > 0 else member_price
    # Provider pre_price occasionally contains a kg/l unit value without a
    # textual unit label. Prefer "unknown ordinary price" over an implausible
    # 5–10x shelf price. Genuine discounts above this threshold are rare enough
    # that they require explicit textual evidence before Kurv displays them.
    if reference >= 5 and normal_price > reference * 4 and normal_price - reference > 60:
        return False
    return True


def _ordinary_text_price(
    prices: list[_PriceCandidate],
    *,
    member_price: float,
) -> float | None:
    candidates = sorted({
        candidate.value
        for candidate in prices
        if not candidate.unit_price_context
        and candidate.value > member_price + 0.005
    })
    return candidates[0] if candidates else None


def detect_member_pricing(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None = None,
) -> MemberPricing | None:
    """Classify club/app/member pricing from textual/structured flyer evidence.

    Rules are intentionally asymmetric: Kurv may miss a weakly documented club
    price, but it must never promote kg/l price data into the ordinary shelf
    price or silently replace the ordinary price with a member price.
    """
    compact = " ".join((text or "").replace("\u00ad", "").split())
    if not compact:
        return None

    label, app_name, programme_match = _program(compact, retailer)
    explicit_markers = [
        match
        for pattern in _EXPLICIT_MARKER_PATTERNS
        for match in pattern.finditer(compact)
    ]
    if not explicit_markers and programme_match is None:
        return None

    prices = _price_candidates(compact)

    # Explicit price labels such as "MEDLEMSPRIS 8,95" / "plus pris 25,-"
    # are stronger than a generic programme logo/name elsewhere in the advert.
    member_price = _rank_member_price(explicit_markers, prices, max_distance=72)
    source = "explicit-member-marker-price"

    if member_price is None and programme_match is not None:
        member_price = _rank_member_price([programme_match], prices, max_distance=100)
        source = "member-program-price"

    # Never infer the club price solely by pairing a primary structured price
    # with a provider pre_price. Build 53 did that and turned føtex 29 kr into a
    # member price because 166,67 kr/kg arrived as pre_price. A member price now
    # requires an actual numeric value tied to a member/program marker.
    if member_price is None:
        return None

    member_price = round(member_price, 2)
    primary_is_member = _same_price(price, member_price)

    ordinary_price: float | None = None
    if price is not None and price > member_price + 0.005:
        # The primary structured price is normally the shelf/campaign price.
        # Reject it only if the flyer itself proves that exact value is a unit
        # price (pr. kg / literpris etc.).
        primary_is_unit = any(
            candidate.unit_price_context and _same_price(candidate.value, price)
            for candidate in prices
        )
        if not primary_is_unit:
            ordinary_price = round(price, 2)
    elif primary_is_member:
        ordinary_price = _ordinary_text_price(prices, member_price=member_price)
        if ordinary_price is None and _normal_price_is_plausible(
            normal_price,
            price=price,
            member_price=member_price,
            prices=prices,
        ):
            ordinary_price = round(normal_price, 2) if normal_price is not None else None
    elif price is None:
        ordinary_price = _ordinary_text_price(prices, member_price=member_price)

    # If the provider's primary price is below the member price, the evidence is
    # contradictory. Do not swap fields or invent a relationship.
    if price is not None and price < member_price - 0.005:
        return None

    if ordinary_price is not None and member_price >= ordinary_price - 0.005:
        return None

    # A separate unit_price string can only invalidate an accidentally matching
    # ordinary value; it can never create a member or ordinary price.
    if ordinary_price is not None and unit_price:
        unit_candidates = _price_candidates(f"pr. kg {unit_price}")
        if any(_same_price(candidate.value, ordinary_price) for candidate in unit_candidates):
            ordinary_price = None

    return MemberPricing(
        ordinary_price=ordinary_price,
        member_price=member_price,
        label=label or _generic_label(compact),
        app_name=app_name,
        requires_activation=True,
        source=source,
        primary_price_was_member=primary_is_member,
    )

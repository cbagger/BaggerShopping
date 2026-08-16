from __future__ import annotations

import re
from dataclasses import dataclass


EXPLICIT_MEMBER_MARKER_RE = re.compile(
    r"\b(?:medlems?[-\s_]?pris|kundeklub[-\s_]?pris|klub[-\s_]?pris|club[-\s_]?price|plus[-\s_]?pris|app[-\s_]?pris|member[-\s_]?price)\b",
    re.IGNORECASE,
)
MEMBERSHIP_PROGRAM_RE = re.compile(
    r"\b(?:lidl\s*plus|netto\s*(?:\+|plus)|(?:føtex|foetex)\s*plus|bilka\s*plus|coop\s*(?:medlem|plus|app)|spar\s*sammen|meny\s*(?:medlem|app))\b",
    re.IGNORECASE,
)
ACTIVATION_RE = re.compile(
    r"\b(?:aktiv[ée]r(?:e|es|et)?|aktiver(?:e|es|et)?|kupon(?:en|er|erne)?|coupon(?:s)?|klip(?:pe|pes|pet)?)\b",
    re.IGNORECASE,
)
ORDINARY_ROLE_RE = re.compile(
    r"\b(?:ikke[-\s]?medlem(?:spris)?|pris\s+ikke[-\s]?medlem|normal[-\s]?pris|almindelig\s+pris|uden\s+medlemskab)\b",
    re.IGNORECASE,
)
BEFORE_ROLE_RE = re.compile(r"\b(?:før[-\s]?pris|før|normalpris\s+før|spar\s+fra)\b", re.IGNORECASE)
MEMBERSHIP_FEE_RE = re.compile(
    r"\b(?:medlemskab|medlemsgebyr|engangsbeløb|oprettelsesgebyr)\b",
    re.IGNORECASE,
)
UNIT_PRICE_BEFORE_RE = re.compile(
    r"(?:\bpr\.?\s*(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml)\b|\b(?:kg|kilo|liter|l)[-\s]?pris\b|\bkr\.?\s*/\s*(?:kg|l)\b|\b(?:kg|kilo)\s*max\.?\b)",
    re.IGNORECASE,
)
UNIT_PRICE_AFTER_RE = re.compile(
    r"^\s*(?:kr\.?\s*)?(?:(?:/\s*)|(?:pr\.?\s*))(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml)\b(?!\s*max)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"(?<!\d)(?:"
    r"(?P<decimal>\d{1,4}[,.]\d{2})"
    r"|(?P<dash>\d{1,4}\s*(?:[,.]|:)\s*[-–])"
    r"|(?P<space>\d{1,3}\s+\d{2})(?=\s*(?:kr\.?|$|[^\d]))"
    r"|(?P<whole>\d{1,4})\s*kr\.?)",
    re.IGNORECASE,
)
PAGE_CONTEXT_OPEN = "[kurv-page-context]"
PAGE_CONTEXT_CLOSE = "[/kurv-page-context]"


@dataclass(frozen=True)
class MemberPricing:
    ordinary_price: float | None
    member_price: float
    label: str
    app_name: str | None
    requires_activation: bool
    source: str
    primary_price_was_member: bool
    confidence: float = 1.0


@dataclass(frozen=True)
class _PriceCandidate:
    start: int
    end: int
    value: float
    unit_price_context: bool
    membership_fee_context: bool
    member_role: bool
    ordinary_role: bool
    before_role: bool
    page_context: bool


def _price_value(match: re.Match[str]) -> float | None:
    raw = match.group(0).casefold().replace("kr", "").strip()
    raw = re.sub(r"\s*(?:[,.]|:)\s*[-–]$", ".00", raw)
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


def _inside_page_context(text: str, position: int) -> bool:
    opened = text.rfind(PAGE_CONTEXT_OPEN, 0, position)
    if opened < 0:
        return False
    closed = text.rfind(PAGE_CONTEXT_CLOSE, 0, position)
    return opened > closed


def _near_before(pattern: re.Pattern[str], text: str, start: int, *, before: int = 55) -> bool:
    left = max(0, start - before)
    return pattern.search(text[left:start]) is not None


def _unit_price_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 44):start]
    if UNIT_PRICE_BEFORE_RE.search(before) is not None:
        return True
    after = text[end:min(len(text), end + 24)]
    return UNIT_PRICE_AFTER_RE.search(after) is not None


def _membership_fee_context(text: str, start: int, end: int) -> bool:
    left = text[max(0, start - 90):start]
    around = left
    if MEMBERSHIP_FEE_RE.search(around) is None:
        return False
    return bool(re.search(r"\b(?:koster|beløb|gebyr|oprettelse|medlemskab)\b", around, re.IGNORECASE))


def _price_candidates(text: str) -> list[_PriceCandidate]:
    result: list[_PriceCandidate] = []
    for match in PRICE_RE.finditer(text):
        value = _price_value(match)
        if value is None:
            continue
        result.append(_PriceCandidate(
            start=match.start(),
            end=match.end(),
            value=value,
            unit_price_context=_unit_price_context(text, match.start(), match.end()),
            membership_fee_context=_membership_fee_context(text, match.start(), match.end()),
            member_role=_near_before(EXPLICIT_MEMBER_MARKER_RE, text, match.start(), before=48),
            ordinary_role=_near_before(ORDINARY_ROLE_RE, text, match.start(), before=44),
            before_role=_near_before(BEFORE_ROLE_RE, text, match.start(), before=28),
            page_context=_inside_page_context(text, match.start()),
        ))
    return result


def has_membership_signal(text: str) -> bool:
    compact = " ".join((text or "").replace("\u00ad", "").split())
    return bool(EXPLICIT_MEMBER_MARKER_RE.search(compact) or MEMBERSHIP_PROGRAM_RE.search(compact))


def _program(text: str, retailer: str) -> tuple[str | None, str | None, re.Match[str] | None]:
    patterns: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
        (re.compile(r"\blidl\s*plus\b", re.IGNORECASE), "Lidl Plus", "Lidl Plus"),
        (re.compile(r"\bnetto\s*(?:\+|plus\b)", re.IGNORECASE), "Netto+", "Netto+"),
        (re.compile(r"\b(?:føtex|foetex)\s*plus\b", re.IGNORECASE), "føtex Plus", "føtex Plus"),
        (re.compile(r"\bbilka\s*plus\b", re.IGNORECASE), "Bilka Plus", "Bilka Plus"),
        (re.compile(r"\bcoop\s*(?:medlem|plus|app)\b", re.IGNORECASE), "Coop medlemspris", "Coop"),
        (re.compile(r"\bspar\s*sammen\b", re.IGNORECASE), "SPAR SAMMEN medlemspris", "SPAR SAMMEN"),
    )
    for pattern, label, app_name in patterns:
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


def _rank_member_candidate(text: str, prices: list[_PriceCandidate], markers: list[re.Match[str]]) -> _PriceCandidate | None:
    ranked: list[tuple[int, int, _PriceCandidate]] = []
    for marker in markers:
        for candidate in prices:
            if candidate.unit_price_context or candidate.membership_fee_context or candidate.ordinary_role or candidate.before_role:
                continue
            if candidate.end < marker.start():
                distance = marker.start() - candidate.end
                direction_penalty = 18
            elif candidate.start > marker.end():
                distance = candidate.start - marker.end()
                direction_penalty = 0
            else:
                distance = 0
                direction_penalty = 0
            if distance <= 90:
                role_bonus = -24 if candidate.member_role else 0
                ranked.append((max(0, distance + direction_penalty + role_bonus), candidate.start, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1]))
    return ranked[0][2]


def _ordinary_text_price(prices: list[_PriceCandidate], *, member_price: float) -> float | None:
    explicit = sorted({
        candidate.value for candidate in prices
        if candidate.ordinary_role
        and not candidate.unit_price_context
        and not candidate.membership_fee_context
        and candidate.value > member_price + 0.005
    })
    if explicit:
        return explicit[0]
    candidates = sorted({
        candidate.value for candidate in prices
        if not candidate.unit_price_context
        and not candidate.membership_fee_context
        and not candidate.before_role
        and candidate.value > member_price + 0.005
    })
    return candidates[0] if candidates else None


def _normal_price_is_plausible(normal_price: float | None, *, price: float | None, member_price: float, prices: list[_PriceCandidate]) -> bool:
    if normal_price is None or normal_price <= member_price + 0.005:
        return False
    if any((candidate.unit_price_context or candidate.membership_fee_context) and _same_price(candidate.value, normal_price) for candidate in prices):
        return False
    reference = price if price is not None and price > 0 else member_price
    if reference >= 5 and normal_price > reference * 4 and normal_price - reference > 60:
        return False
    return True


def detect_member_pricing(*, retailer: str, price: float | None, normal_price: float | None, text: str, unit_price: str | None = None) -> MemberPricing | None:
    compact = " ".join((text or "").replace("\u00ad", "").split())
    if not compact:
        return None

    label, app_name, programme_match = _program(compact, retailer)
    explicit_markers = list(EXPLICIT_MEMBER_MARKER_RE.finditer(compact))
    markers = list(explicit_markers)
    if programme_match is not None and all(programme_match.span() != match.span() for match in markers):
        markers.append(programme_match)
    if not markers:
        return None

    prices = _price_candidates(compact)
    selected = _rank_member_candidate(compact, prices, markers)
    if selected is None:
        return None

    member_price = selected.value
    primary_is_member = _same_price(price, member_price)
    ordinary_price: float | None = None

    if price is not None and price > member_price + 0.005:
        source_candidate = next((candidate for candidate in prices if _same_price(candidate.value, price)), None)
        if source_candidate is None or not (source_candidate.unit_price_context or source_candidate.membership_fee_context or source_candidate.before_role):
            ordinary_price = round(price, 2)
    else:
        ordinary_price = _ordinary_text_price(prices, member_price=member_price)
        if ordinary_price is None and _normal_price_is_plausible(normal_price, price=price, member_price=member_price, prices=prices):
            ordinary_price = round(normal_price, 2) if normal_price is not None else None

    if price is not None and price < member_price - 0.005:
        return None
    if ordinary_price is not None and member_price >= ordinary_price - 0.005:
        return None

    if ordinary_price is not None and unit_price:
        unit_candidates = _price_candidates(f"pr. kg {unit_price}")
        if any(_same_price(candidate.value, ordinary_price) for candidate in unit_candidates):
            ordinary_price = None

    page_only = selected.page_context and not any(not candidate.page_context and candidate.member_role and _same_price(candidate.value, member_price) for candidate in prices)
    source = "page-context-member-price" if page_only else "structured-member-price"
    confidence = 0.72 if page_only else (0.99 if selected.member_role else 0.94)

    return MemberPricing(
        ordinary_price=ordinary_price,
        member_price=member_price,
        label=label or _generic_label(compact),
        app_name=app_name,
        requires_activation=bool(ACTIVATION_RE.search(compact)),
        source=source,
        primary_price_was_member=primary_is_member,
        confidence=confidence,
    )

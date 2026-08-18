from __future__ import annotations

import re

from . import member_pricing_v3 as v3


MemberPricing = v3.MemberPricing

_PAGE_CONTEXT_RE = re.compile(
    re.escape(v3.PAGE_CONTEXT_OPEN) + r".*?" + re.escape(v3.PAGE_CONTEXT_CLOSE),
    re.IGNORECASE | re.DOTALL,
)

# These phrases are intentionally broader than customer-facing price markers.
# They are useful for source enrichment / Luna review, but by themselves do not
# justify painting a red member-price badge.
_BROAD_MEMBERSHIP_RE = re.compile(
    r"(?:"
    r"\bmedlems?køb\b|"
    r"\bmedlems?fordel(?:e|ene)?\b|"
    r"\bmedlems?rabat(?:ter)?\b|"
    r"\bmånedens\s+medlems?\w*\b|"
    r"\bpris\s+(?:når\s+du\s+er|som)\s+medlem\b|"
    r"\b(?:kun\s+)?for\s+medlemmer(?:ne)?\b|"
    r"\bsom\s+medlem\b|"
    r"\bcoop\s+medlem\b|"
    r"\bbliv\s+coop\s+medlem\b"
    r")",
    re.IGNORECASE,
)

# Context phrases that explicitly introduce a following member price. These are
# stronger than generic membership marketing, but still separate from retailer
# programme names such as "føtex Plus", which may also appear next to an
# ordinary price in legal/access text.
_DIRECT_CONTEXT_PRICE_RE = re.compile(
    r"\bpris\s+(?:når\s+du\s+er|som)\s+medlem\b",
    re.IGNORECASE,
)

# Unit/reference prices are not the same thing as a selling price written
# "PR. STK. 29,-". kg/l/100g/100ml are inherently comparison units; a per-piece
# value is only treated as comparison pricing when the advert explicitly marks
# it as max/maks (the live Neophos shape: "Pr. stk. max. 1,98").
_UNIT_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?<!\d)(?P<price>\d{1,4}(?:[,.]\d{1,2})?)\s*kr\.?\s*(?:/|pr\.?)\s*"
        r"(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpr\.?\s*(?:kg|kilo|l(?:iter)?|100\s*g|100\s*ml)\b"
        r"[^\d]{0,24}(?P<price>\d{1,4}(?:[,.]\d{1,2})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpr\.?\s*(?:stk\.?|styk(?:ker)?)\s*(?:max|maks)\.?\s*"
        r"(?:kr\.?\s*)?(?P<price>\d{1,4}(?:[,.]\d{1,2})?)",
        re.IGNORECASE,
    ),
)


def _compact(text: str) -> str:
    return " ".join((text or "").replace("\u00ad", "").split())


def _customer_text(text: str) -> str:
    """Remove page-only neighbour context before deterministic badge logic."""
    return _compact(_PAGE_CONTEXT_RE.sub(" ", text or ""))


def has_membership_signal(text: str) -> bool:
    compact = _compact(text)
    return bool(v3.has_membership_signal(compact) or _BROAD_MEMBERSHIP_RE.search(compact))


def _retailer_program(retailer: str, text: str) -> tuple[str, str | None]:
    label, app_name, _ = v3._program(text, retailer)
    if label:
        return label, app_name

    key = retailer.casefold().strip()
    if key in {"365discount", "365 discount", "coop 365", "coop365"}:
        return "Coop medlemspris", "Coop-appen"
    if key == "meny":
        return "MENY medlemspris", "MENY-appen"
    if key == "netto":
        return "Netto+", "Netto+"
    if key in {"føtex", "foetex"}:
        return "føtex Plus", "føtex Plus"
    if key == "bilka":
        return "Bilka Plus", "Bilka Plus"
    if key == "lidl":
        return "Lidl Plus", "Lidl Plus"
    if key == "spar" and re.search(r"\bspar\s+sammen\b", text, re.IGNORECASE):
        return "SPAR SAMMEN medlemspris", "SPAR SAMMEN"
    return "Medlemspris", None


def _direct_program_marker(text: str, retailer: str) -> re.Match[str] | None:
    """Return a programme mention only when it directly acts as a price role.

    "Med Lidl Plus 12,-" is strong evidence. "Gælder kun med føtex Plus appen"
    is access/legal text and must never make the nearest ordinary price a member
    price. Explicit PLUS PRIS / + PRIS / MEDLEMSPRIS markers are handled first.
    """
    _, _, programme = v3._program(text, retailer)
    if programme is None:
        return None

    prefix = text[max(0, programme.start() - 32):programme.start()].casefold()
    if re.search(r"gælder\s+(?:kun\s+)?med\s*$", prefix):
        return None
    if re.search(r"\bmed\s*$", prefix):
        return programme
    return None


def _ordinary_price(
    *,
    text: str,
    prices: list[v3._PriceCandidate],
    price: float | None,
    normal_price: float | None,
    member_price: float,
    unit_price: str | None,
) -> float | None:
    primary_is_member = v3._same_price(price, member_price)
    ordinary: float | None = None

    if price is not None and price > member_price + 0.005:
        source_candidate = next(
            (candidate for candidate in prices if v3._same_price(candidate.value, price)),
            None,
        )
        if source_candidate is None or not (
            source_candidate.unit_price_context
            or source_candidate.membership_fee_context
            or source_candidate.before_role
        ):
            ordinary = round(price, 2)
    else:
        ordinary = v3._ordinary_text_price(prices, member_price=member_price)
        if ordinary is None and v3._normal_price_is_plausible(
            normal_price,
            price=price,
            member_price=member_price,
            prices=prices,
        ):
            ordinary = round(normal_price, 2) if normal_price is not None else None

    if primary_is_member and v3.ORDINARY_PRICE_RANGE_RE.search(text):
        ordinary = None

    if ordinary is not None and unit_price:
        unit_candidates = v3._price_candidates(f"pr. kg {unit_price}")
        if any(v3._same_price(candidate.value, ordinary) for candidate in unit_candidates):
            ordinary = None

    return ordinary


def _build_from_selected(
    *,
    retailer: str,
    text: str,
    price: float | None,
    normal_price: float | None,
    unit_price: str | None,
    selected: v3._PriceCandidate,
    source: str,
    confidence: float,
) -> MemberPricing | None:
    member_price = selected.value

    # Final structured-price safety net: a value that the same advert describes
    # as kg/l/100g/100ml or explicit max-per-piece comparison pricing can never
    # become the customer member price merely because a PLUS/MEDLEMSPRIS marker
    # is textually nearby. Reject that deterministic interpretation and let v3
    # consult Luna's verified visual pricing record instead.
    unit_values = _unit_price_values(text, unit_price)
    if selected.unit_price_context or any(
        v3._same_price(member_price, value) for value in unit_values
    ):
        return None

    if price is not None and price < member_price - 0.005:
        return None

    prices = v3._price_candidates(text)
    ordinary = _ordinary_price(
        text=text,
        prices=prices,
        price=price,
        normal_price=normal_price,
        member_price=member_price,
        unit_price=unit_price,
    )
    if ordinary is not None and any(
        v3._same_price(ordinary, value) for value in unit_values
    ):
        ordinary = None
    if ordinary is not None and member_price >= ordinary - 0.005:
        return None

    label, app_name = _retailer_program(retailer, text)
    return MemberPricing(
        ordinary_price=ordinary,
        member_price=member_price,
        label=label,
        app_name=app_name,
        requires_activation=bool(v3.ACTIVATION_RE.search(text)),
        source=source,
        primary_price_was_member=v3._same_price(price, member_price),
        confidence=confidence,
    )


def _strong_deterministic(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None,
) -> MemberPricing | None:
    """Resolve customer-visible roles before consulting Luna.

    Direct price-role evidence is authoritative because it is the clearest
    evidence available in the advert itself. Luna may help with uncertain or
    image-only cases, but may never invert an explicit `+ PRIS`, `PLUS PRIS` or
    `MEDLEMSPRIS` relationship.
    """
    local = _customer_text(text)
    if not local:
        return None

    prices = v3._price_candidates(local)
    explicit_markers = list(v3.EXPLICIT_MEMBER_MARKER_RE.finditer(local))
    if explicit_markers:
        selected = v3._rank_member_candidate(prices, explicit_markers)
        if selected is not None and selected.member_role:
            result = _build_from_selected(
                retailer=retailer,
                text=local,
                price=price,
                normal_price=normal_price,
                unit_price=unit_price,
                selected=selected,
                source="structured-explicit-member-price-v4",
                confidence=0.995,
            )
            if result is not None:
                return result

    # "Pris når du er medlem 18,-" is also a direct role statement.
    context_markers = list(_DIRECT_CONTEXT_PRICE_RE.finditer(local))
    if context_markers:
        selected = v3._rank_member_candidate(prices, context_markers)
        if selected is not None:
            result = _build_from_selected(
                retailer=retailer,
                text=local,
                price=price,
                normal_price=normal_price,
                unit_price=unit_price,
                selected=selected,
                source="structured-member-role-context-v4",
                confidence=0.985,
            )
            if result is not None:
                return result

    # Programme names are only price evidence in a direct construction such as
    # "Med Lidl Plus 12,-". Generic "Gælder kun med ... appen" access text is
    # deliberately excluded so it cannot steal the ordinary price role.
    programme_marker = _direct_program_marker(local, retailer)
    if programme_marker is not None:
        selected = v3._rank_member_candidate(prices, [programme_marker])
        if selected is not None:
            result = _build_from_selected(
                retailer=retailer,
                text=local,
                price=price,
                normal_price=normal_price,
                unit_price=unit_price,
                selected=selected,
                source="structured-direct-program-price-v4",
                confidence=0.985,
            )
            if result is not None:
                return result

    # Some providers expose the campaign price as their primary structured
    # price while OCR/text only says e.g. "Månedens medlemskøb" and explicitly
    # labels a higher non-member price. In that narrow situation the roles are
    # still unambiguous enough to classify without vision/Luna.
    if price is not None and _BROAD_MEMBERSHIP_RE.search(local):
        explicit_ordinary = sorted({
            candidate.value
            for candidate in prices
            if candidate.ordinary_role
            and not candidate.unit_price_context
            and not candidate.membership_fee_context
            and candidate.value > price + 0.005
        })
        if explicit_ordinary:
            ordinary = explicit_ordinary[0]
            label, app_name = _retailer_program(retailer, local)
            return MemberPricing(
                ordinary_price=ordinary,
                member_price=round(price, 2),
                label=label,
                app_name=app_name,
                requires_activation=bool(v3.ACTIVATION_RE.search(local)),
                source="structured-membership-context-v4",
                primary_price_was_member=True,
                confidence=0.98,
            )

    return None


def _unit_price_values(*values: str | None) -> set[float]:
    result: set[float] = set()
    for value in values:
        if not value:
            continue
        for pattern in _UNIT_PRICE_PATTERNS:
            for match in pattern.finditer(value):
                try:
                    number = float(match.group("price").replace(",", "."))
                except (AttributeError, ValueError):
                    continue
                if 0 < number <= 10_000:
                    result.add(round(number, 2))
    return result


def _unsafe_luna_member_role(
    *,
    price: float | None,
    text: str,
    unit_price: str | None,
    pricing: MemberPricing | None,
) -> bool:
    """Fail closed cached AI pricing that still violates product-price sanity.

    This is only a presentation guard. The semantic audit layer separately
    escalates the same shapes to a targeted visual crop, so valid unusual deals
    can become visible again once their roles are actually verified.
    """
    if pricing is None or pricing.source != "luna-verified":
        return False

    # Provider primary price merely relabelled as member price, with no ordinary
    # role resolved (the live Seafoodmix failure).
    if (
        price is not None
        and pricing.ordinary_price is None
        and v3._same_price(price, pricing.member_price)
    ):
        return True

    unit_values = _unit_price_values(_customer_text(text), unit_price)
    if any(v3._same_price(pricing.member_price, value) for value in unit_values):
        return True

    reference = pricing.ordinary_price if pricing.ordinary_price is not None else price
    if (
        isinstance(reference, (int, float))
        and reference > pricing.member_price > 0
        and reference - pricing.member_price >= 10
        and pricing.member_price / reference <= 0.25
        and unit_values
    ):
        # If a supposed member price is extreme and the advert/provider also
        # contains a low unit-price value in the same numerical neighbourhood,
        # do not show it before the new targeted crop has resolved the roles.
        if any(
            abs(pricing.member_price - value) / max(pricing.member_price, value) <= 0.20
            for value in unit_values
            if value > 0
        ):
            return True

    return False


def detect_member_pricing(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None = None,
) -> MemberPricing | None:
    compact = _compact(text)
    if not compact:
        return None

    deterministic = _strong_deterministic(
        retailer=retailer,
        price=price,
        normal_price=normal_price,
        text=compact,
        unit_price=unit_price,
    )
    if deterministic is not None:
        return deterministic

    # Fall back to v3 for page-only/Luna-verified and legacy safe cases. Direct
    # advert roles have already had first priority. Remaining AI facts fail
    # closed whenever they still look like an unresolved provider/unit-price
    # role; the semantic audit contract will then send them to a visual crop.
    fallback = v3.detect_member_pricing(
        retailer=retailer,
        price=price,
        normal_price=normal_price,
        text=compact,
        unit_price=unit_price,
    )
    if _unsafe_luna_member_role(
        price=price,
        text=compact,
        unit_price=unit_price,
        pricing=fallback,
    ):
        return None
    return fallback


__all__ = ["MemberPricing", "detect_member_pricing", "has_membership_signal"]

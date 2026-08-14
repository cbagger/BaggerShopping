from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .households import HouseholdContext, current_household, load_store as load_household_store, require_household, update_household

router = APIRouter(prefix="/api/mobile/v1/product-identity", tags=["product-identity"])
LOCK = asyncio.Lock()

STOPWORDS = {
    "af", "den", "det", "eller", "fra", "i", "med", "og", "pak", "pk",
    "pr", "på", "stk", "til", "uge", "x",
}
PROMO_WORDS = {
    "billig", "kun", "maks", "mix", "salg", "særlig", "tilbud", "vælg",
}
TYPE_ALIASES = {
    "zero": "zero", "sukkerfri": "zero", "light": "light", "let": "light",
    "økologisk": "organic", "øko": "organic", "organic": "organic",
    "laktosefri": "lactose_free", "glutenfri": "gluten_free",
    "alkoholfri": "alcohol_free",
}
FLAVOURS = {
    "appelsin", "citron", "cola", "jordbær", "karamel", "lime", "mango",
    "naturel", "pebermynte", "salt", "saltet", "salted", "vanilje",
}
KNOWN_BRANDS = {
    "arla", "carlsberg", "coca cola", "coca-cola", "danone", "kærgården",
    "lambi", "lurpak", "merrild", "nestlé", "nutella", "pepsi", "royal",
    "schulstad", "tuborg", "whiskas",
}
UNIT_FACTORS = {
    "g": ("mass", 1.0), "kg": ("mass", 1000.0),
    "ml": ("volume", 1.0), "cl": ("volume", 10.0), "l": ("volume", 1000.0),
    "stk": ("count", 1.0), "pk": ("count", 1.0),
}
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?|[a-zæøå%]+", re.IGNORECASE)
AMOUNT_RE = re.compile(
    r"(?:(?P<count>\d+)\s*[x×]\s*)?(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|ml|cl|l|stk|pk)\b",
    re.IGNORECASE,
)
RANGE_AMOUNT_RE = re.compile(
    r"(?P<minimum>\d+(?:[.,]\d+)?)\s*(?:-|–|—|til)\s*(?P<maximum>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|ml|cl|l|stk)\b",
    re.IGNORECASE,
)
PACK_ONLY_RE = re.compile(r"(?P<count>\d+)\s*(?:pak|pk|stk)\b", re.IGNORECASE)


class ProductAnalysis(BaseModel):
    original: str
    normalized: str
    brand: str | None = None
    product: str
    variant: str | None = None
    flavours: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    size: float | None = None
    unit: str | None = None
    pack_count: int = 1
    total_amount: float | None = None
    total_amount_min: float | None = None
    total_amount_max: float | None = None
    amount_dimension: str | None = None
    unit_price: float | None = None
    unit_price_min: float | None = None
    unit_price_max: float | None = None
    unit_price_unit: str | None = None
    amount_text: str | None = None
    canonical_id: str | None = None


class MatchResult(BaseModel):
    level: Literal["same_item", "compatible_variant", "probably_same", "not_same"]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    direct_price_comparison: bool = False
    left: ProductAnalysis
    right: ProductAnalysis


class CompareRequest(BaseModel):
    left: str = Field(min_length=1, max_length=300)
    right: str = Field(min_length=1, max_length=300)
    left_quantity: float | None = Field(default=None, gt=0)
    left_unit: str | None = None
    right_quantity: float | None = Field(default=None, gt=0)
    right_unit: str | None = None
    left_price: float | None = Field(default=None, gt=0)
    right_price: float | None = Field(default=None, gt=0)


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = None
    price: float | None = Field(default=None, gt=0)


class FeedbackRequest(CompareRequest):
    decision: Literal["same_item", "compatible_variant", "never_match"]


class FamilyProductPreference(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    preferred_name: str = Field(min_length=1, max_length=300)
    mode: Literal["preferred", "required", "any_variant"] = "preferred"


class FamilyPreferencesResponse(BaseModel):
    preferences: list[FamilyProductPreference]


def store_path() -> Path:
    return Path(os.getenv("PRODUCT_IDENTITY_STORE_PATH", "/data/product-identity.json"))


def _fold(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in folded if not unicodedata.combining(character))


def normalize(value: str) -> str:
    value = value.replace("-", " ").replace("/", " ")
    return " ".join(TOKEN_RE.findall(_fold(value)))


def _load_store() -> dict[str, Any]:
    try:
        data = json.loads(store_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_store(store: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    temporary.replace(path)


def _pair_key(left: str, right: str) -> str:
    return "|".join(sorted((normalize(left), normalize(right))))


def family_preference(item_name: str) -> FamilyProductPreference | None:
    """Read only the current family's private preference envelope."""
    try:
        context = current_household()
    except HTTPException:
        return None
    household = load_household_store().get("households", {}).get(context.household_id, {})
    value = household.get("product_preferences", {}).get(normalize(item_name))
    try:
        return FamilyProductPreference.model_validate(value) if value else None
    except Exception:
        return None


def apply_family_preference(item_name: str, candidate: str, score: int, result: MatchResult) -> tuple[int, MatchResult]:
    preference = family_preference(item_name)
    if not preference:
        return score, result
    if preference.mode == "any_variant":
        return score, result
    preferred_match = compare(preference.preferred_name, candidate)
    accepted = preferred_match.level in {"same_item", "probably_same"}
    if preference.mode == "required" and not accepted:
        rejected = result.model_copy(update={
            "level": "not_same",
            "confidence": .99,
            "explanation": "Matcher ikke familiens krævede varevariant.",
            "direct_price_comparison": False,
        })
        return 0, rejected
    if accepted:
        boosted = result.model_copy(update={
            "explanation": f"{result.explanation} Matcher familiens foretrukne variant.",
        })
        return score + 14, boosted
    return score, result


def _external_amount(quantity: float | None, unit: str | None) -> tuple[float | None, str | None]:
    normalized_unit = normalize(unit or "")
    if quantity is None or normalized_unit not in UNIT_FACTORS:
        return None, None
    dimension, factor = UNIT_FACTORS[normalized_unit]
    return quantity * factor, dimension


def analyze(
    value: str,
    *,
    quantity: float | None = None,
    unit: str | None = None,
    price: float | None = None,
) -> ProductAnalysis:
    normalized = normalize(value)
    tokens = normalized.split()
    types = sorted({TYPE_ALIASES[token] for token in tokens if token in TYPE_ALIASES})
    flavours = sorted({token for token in tokens if token in FLAVOURS})

    amount_source = _fold(value).replace("/", " ")
    range_match = RANGE_AMOUNT_RE.search(amount_source)
    amount_match = None if range_match else AMOUNT_RE.search(amount_source)
    size = parsed_unit = total = total_min = total_max = dimension = None
    pack_count = 1
    amount_text = None
    if range_match:
        minimum = float(range_match.group("minimum").replace(",", "."))
        maximum = float(range_match.group("maximum").replace(",", "."))
        parsed_unit = range_match.group("unit").casefold()
        dimension, factor = UNIT_FACTORS[parsed_unit]
        total_min, total_max = minimum * factor, maximum * factor
        size = minimum
        amount_text = f"{minimum:g}–{maximum:g} {parsed_unit}"
    elif amount_match:
        pack_count = int(amount_match.group("count") or 1)
        size = float(amount_match.group("amount").replace(",", "."))
        parsed_unit = amount_match.group("unit").casefold()
        dimension, factor = UNIT_FACTORS[parsed_unit]
        total = size * factor * pack_count
        total_min = total_max = total
        amount_text = f"{pack_count} × {size:g} {parsed_unit}" if pack_count > 1 else f"{size:g} {parsed_unit}"
    else:
        total, dimension = _external_amount(quantity, unit)
        if total is not None:
            size, parsed_unit = quantity, normalize(unit or "")
            total_min = total_max = total
            amount_text = f"{quantity:g} {parsed_unit}"
        else:
            pack_match = PACK_ONLY_RE.search(normalized)
            if pack_match:
                pack_count = int(pack_match.group("count"))
                size, parsed_unit, dimension = float(pack_count), "stk", "count"
                total = total_min = total_max = float(pack_count)
                amount_text = f"{pack_count} stk"

    brand = None
    folded_brands = sorted({_fold(item).replace("-", " ") for item in KNOWN_BRANDS}, key=len, reverse=True)
    for candidate in folded_brands:
        if normalized == candidate or normalized.startswith(candidate + " "):
            brand = candidate
            break

    excluded = STOPWORDS | PROMO_WORDS | set(TYPE_ALIASES) | FLAVOURS | {"variant"}
    matched_amount = range_match or amount_match
    amount_tokens = set(TOKEN_RE.findall(normalize(matched_amount.group(0)))) if matched_amount else set()
    product_tokens = [
        token for token in tokens
        if token not in excluded and token not in amount_tokens
        and not token.replace(",", "").replace(".", "").isdigit()
        and token != "%"
    ]
    if brand:
        brand_tokens = brand.split()
        if product_tokens[:len(brand_tokens)] == brand_tokens:
            product_tokens = product_tokens[len(brand_tokens):]
    product = " ".join(product_tokens) or normalized

    store = _load_store()
    canonical_id = store.get("aliases", {}).get(normalized)
    unit_price = unit_price_min = unit_price_max = unit_price_unit = None
    if price is not None and total is not None and total > 0:
        basis = 1000.0 if dimension in {"mass", "volume"} else 1.0
        unit_price = price / total * basis
        unit_price_min = unit_price_max = unit_price
        unit_price_unit = "kg" if dimension == "mass" else "l" if dimension == "volume" else "stk"
    elif price is not None and total_min and total_max:
        basis = 1000.0 if dimension in {"mass", "volume"} else 1.0
        unit_price_min = price / total_max * basis
        unit_price_max = price / total_min * basis
        unit_price_unit = "kg" if dimension == "mass" else "l" if dimension == "volume" else "stk"

    return ProductAnalysis(
        original=value, normalized=normalized, brand=brand, product=product,
        variant=" ".join(flavours + types) or None, flavours=flavours, types=types,
        size=size, unit=parsed_unit, pack_count=pack_count, total_amount=total,
        total_amount_min=total_min, total_amount_max=total_max,
        amount_dimension=dimension, unit_price=unit_price,
        unit_price_min=unit_price_min, unit_price_max=unit_price_max,
        unit_price_unit=unit_price_unit,
        amount_text=amount_text, canonical_id=canonical_id,
    )


def _product_overlap(left: ProductAnalysis, right: ProductAnalysis) -> float:
    left_tokens, right_tokens = set(left.product.split()), set(right.product.split())
    if not left_tokens or not right_tokens:
        return 0
    if left.canonical_id and left.canonical_id == right.canonical_id:
        return 1
    intersection = left_tokens & right_tokens
    if not intersection:
        # Conservative Danish compound support: sødmælk may match mælk, but
        # short fragments such as æg inside pålæg never do.
        if any(
            len(a) >= 4 and len(b) >= 4 and (a.endswith(b) or b.endswith(a))
            for a in left_tokens for b in right_tokens
        ):
            return 0.65
        return 0
    return len(intersection) / max(len(left_tokens), len(right_tokens))


def compare(
    left_text: str,
    right_text: str,
    *,
    left_quantity: float | None = None,
    left_unit: str | None = None,
    right_quantity: float | None = None,
    right_unit: str | None = None,
    left_price: float | None = None,
    right_price: float | None = None,
) -> MatchResult:
    left = analyze(left_text, quantity=left_quantity, unit=left_unit, price=left_price)
    right = analyze(right_text, quantity=right_quantity, unit=right_unit, price=right_price)
    store = _load_store()
    learned = store.get("matches", {}).get(_pair_key(left_text, right_text))
    if learned == "never_match":
        return MatchResult(level="not_same", confidence=1, explanation="Match afvist af fælles produktviden.", left=left, right=right)

    if left.brand and right.brand and left.brand != right.brand:
        return MatchResult(level="not_same", confidence=.98, explanation="Forskellige mærker.", left=left, right=right)

    overlap = _product_overlap(left, right)
    if overlap == 0:
        return MatchResult(level="not_same", confidence=.97, explanation="Forskellige grundprodukter.", left=left, right=right)

    conflicting_types = bool(set(left.types) ^ set(right.types)) and bool(left.types or right.types)
    conflicting_flavours = bool(left.flavours and right.flavours and set(left.flavours) != set(right.flavours))
    if conflicting_types:
        if (set(left.types) ^ set(right.types)) <= {"light"}:
            return MatchResult(
                level="compatible_variant", confidence=.92,
                explanation="Samme produktfamilie, men almindelig og light/let må ikke behandles som samme vare.",
                left=left, right=right,
            )
        return MatchResult(level="not_same", confidence=.96, explanation="Afgørende type er forskellig, eksempelvis zero, light, økologisk eller fri-variant.", left=left, right=right)
    if conflicting_flavours:
        return MatchResult(level="compatible_variant", confidence=.94, explanation="Samme produktfamilie, men smagsvarianterne er forskellige.", left=left, right=right)

    left_amount_known = left.total_amount_min is not None and left.total_amount_max is not None
    right_amount_known = right.total_amount_min is not None and right.total_amount_max is not None
    amounts_known = left_amount_known and right_amount_known
    compatible_amount = (
        amounts_known
        and left.amount_dimension == right.amount_dimension
        and left.total_amount is not None and right.total_amount is not None
        and abs(left.total_amount - right.total_amount) < .01
    )
    if amounts_known and not compatible_amount:
        return MatchResult(
            level="compatible_variant", confidence=.9,
            explanation="Samme produktfamilie, men forskellig samlet pakkestørrelse.",
            direct_price_comparison=False, left=left, right=right,
        )

    if learned == "same_item":
        level, confidence, explanation = "same_item", .99, "Bekræftet af fælles produktviden."
    elif learned == "compatible_variant":
        level, confidence, explanation = "compatible_variant", .98, "Bekræftet som kompatibel variant af fælles produktviden."
    elif left.normalized == right.normalized or (overlap == 1 and left.brand == right.brand and compatible_amount):
        level, confidence, explanation = "same_item", .95, "Samme mærke, grundprodukt og kompatibel mængde."
    elif overlap >= .5:
        level, confidence, explanation = "probably_same", .72, "Samme grundprodukt, men ikke nok oplysninger til et sikkert automatisk match."
    else:
        level, confidence, explanation = "not_same", .8, "For lidt fælles produktinformation."

    both_amounts_missing = not left_amount_known and not right_amount_known
    return MatchResult(
        level=level, confidence=confidence, explanation=explanation,
        direct_price_comparison=level == "same_item" and (compatible_amount or (both_amounts_missing and left.normalized == right.normalized)),
        left=left, right=right,
    )


@router.post("/analyze", response_model=ProductAnalysis)
async def analyze_product(request: AnalyzeRequest) -> ProductAnalysis:
    return analyze(request.text, quantity=request.quantity, unit=request.unit, price=request.price)


@router.post("/compare", response_model=MatchResult)
async def compare_products(request: CompareRequest) -> MatchResult:
    return compare(
        request.left, request.right,
        left_quantity=request.left_quantity, left_unit=request.left_unit,
        right_quantity=request.right_quantity, right_unit=request.right_unit,
        left_price=request.left_price, right_price=request.right_price,
    )


@router.post("/feedback")
async def product_feedback(request: FeedbackRequest) -> dict[str, Any]:
    left, right = analyze(request.left), analyze(request.right)
    if request.decision == "same_item" and _product_overlap(left, right) == 0:
        raise HTTPException(status_code=409, detail="Match kan ikke læres: grundprodukterne er modstridende")
    async with LOCK:
        store = _load_store()
        store.setdefault("matches", {})[_pair_key(request.left, request.right)] = request.decision
        if request.decision in {"same_item", "compatible_variant"}:
            canonical = left.canonical_id or right.canonical_id or "product:" + normalize(left.product).replace(" ", "-")
            store.setdefault("aliases", {})[left.normalized] = canonical
            store.setdefault("aliases", {})[right.normalized] = canonical
        _save_store(store)
    return {"ok": True, "decision": request.decision}


@router.get("/preferences", response_model=FamilyPreferencesResponse)
async def list_family_preferences(
    context: HouseholdContext = Depends(require_household),
) -> FamilyPreferencesResponse:
    store = load_household_store()
    household = store.get("households", {}).get(context.household_id, {})
    values = household.get("product_preferences", {}).values()
    return FamilyPreferencesResponse(
        preferences=[FamilyProductPreference.model_validate(value) for value in values]
    )


@router.put("/preferences")
async def set_family_preference(
    request: FamilyProductPreference,
    context: HouseholdContext = Depends(require_household),
) -> dict[str, Any]:
    preference = request.model_copy(update={
        "item_name": " ".join(request.item_name.split()),
        "preferred_name": " ".join(request.preferred_name.split()),
    })

    def mutate(household: dict[str, Any]) -> None:
        household.setdefault("product_preferences", {})[normalize(preference.item_name)] = preference.model_dump()

    await update_household(context, mutate)
    return {"ok": True, "preference": preference.model_dump()}


@router.delete("/preferences/{item_key}")
async def remove_family_preference(
    item_key: str,
    context: HouseholdContext = Depends(require_household),
) -> dict[str, Any]:
    def mutate(household: dict[str, Any]) -> bool:
        return household.setdefault("product_preferences", {}).pop(normalize(item_key), None) is not None

    return {"ok": True, "removed": await update_household(context, mutate)}

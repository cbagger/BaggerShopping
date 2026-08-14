from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
    amount_dimension: str | None = None
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


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = None


class FeedbackRequest(CompareRequest):
    decision: Literal["same_item", "compatible_variant", "never_match"]


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


def _external_amount(quantity: float | None, unit: str | None) -> tuple[float | None, str | None]:
    normalized_unit = normalize(unit or "")
    if quantity is None or normalized_unit not in UNIT_FACTORS:
        return None, None
    dimension, factor = UNIT_FACTORS[normalized_unit]
    return quantity * factor, dimension


def analyze(value: str, *, quantity: float | None = None, unit: str | None = None) -> ProductAnalysis:
    normalized = normalize(value)
    tokens = normalized.split()
    types = sorted({TYPE_ALIASES[token] for token in tokens if token in TYPE_ALIASES})
    flavours = sorted({token for token in tokens if token in FLAVOURS})

    amount_match = AMOUNT_RE.search(normalized)
    size = parsed_unit = total = dimension = None
    pack_count = 1
    if amount_match:
        pack_count = int(amount_match.group("count") or 1)
        size = float(amount_match.group("amount").replace(",", "."))
        parsed_unit = amount_match.group("unit").casefold()
        dimension, factor = UNIT_FACTORS[parsed_unit]
        total = size * factor * pack_count
    else:
        total, dimension = _external_amount(quantity, unit)
        if total is not None:
            size, parsed_unit = quantity, normalize(unit or "")

    brand = None
    folded_brands = sorted({_fold(item).replace("-", " ") for item in KNOWN_BRANDS}, key=len, reverse=True)
    for candidate in folded_brands:
        if normalized == candidate or normalized.startswith(candidate + " "):
            brand = candidate
            break

    excluded = STOPWORDS | PROMO_WORDS | set(TYPE_ALIASES) | FLAVOURS | {"variant"}
    amount_tokens = set(TOKEN_RE.findall(amount_match.group(0))) if amount_match else set()
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
    return ProductAnalysis(
        original=value, normalized=normalized, brand=brand, product=product,
        variant=" ".join(flavours + types) or None, flavours=flavours, types=types,
        size=size, unit=parsed_unit, pack_count=pack_count, total_amount=total,
        amount_dimension=dimension, canonical_id=canonical_id,
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
) -> MatchResult:
    left = analyze(left_text, quantity=left_quantity, unit=left_unit)
    right = analyze(right_text, quantity=right_quantity, unit=right_unit)
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

    amounts_known = left.total_amount is not None and right.total_amount is not None
    compatible_amount = (
        amounts_known
        and left.amount_dimension == right.amount_dimension
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

    both_amounts_missing = left.total_amount is None and right.total_amount is None
    return MatchResult(
        level=level, confidence=confidence, explanation=explanation,
        direct_price_comparison=level == "same_item" and (compatible_amount or (both_amounts_missing and left.normalized == right.normalized)),
        left=left, right=right,
    )


@router.post("/analyze", response_model=ProductAnalysis)
async def analyze_product(request: AnalyzeRequest) -> ProductAnalysis:
    return analyze(request.text, quantity=request.quantity, unit=request.unit)


@router.post("/compare", response_model=MatchResult)
async def compare_products(request: CompareRequest) -> MatchResult:
    return compare(
        request.left, request.right,
        left_quantity=request.left_quantity, left_unit=request.left_unit,
        right_quantity=request.right_quantity, right_unit=request.right_unit,
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

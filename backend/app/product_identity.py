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
from .flyer_intelligence import extract_variants

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
COMPOUND_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("whole_milk", re.compile(r"\bsødmælk\b")),
    ("low_fat_milk", re.compile(r"\bletmælk\b")),
    ("mini_milk", re.compile(r"\bminimælk\b")),
    ("skimmed_milk", re.compile(r"\bskummetmælk\b")),
)
FLAVOURS = {
    "appelsin", "citron", "cola", "jordbær", "karamel", "lime", "mango",
    "naturel", "pebermynte", "salt", "saltet", "salted", "vanilje",
}
KNOWN_BRANDS = {
    "arla", "carlsberg", "coca cola", "coca-cola", "coop", "danone",
    "floralys", "harboe", "jolly", "kærgården", "lambi", "lurpak",
    "merrild", "milbona", "nestlé", "nutella", "pepsi", "rema 1000",
    "royal", "schulstad", "tuborg", "whiskas", "xtra",
}
CANONICAL_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cola", re.compile(r"\b(cola|coca\s*cola|pepsi)\b")),
    ("soft_drink", re.compile(r"\b(sodavand|fanta|sprite|squash|schweppes)\b")),
    ("bread", re.compile(r"\b[\wæøå]*(?:brød|toast)\b")),
    ("fermented_dairy", re.compile(r"\b(yoghurt|yogurt|skyr)\b")),
    ("butter_spread", re.compile(r"\b(smør|smørbar|kærgården|lurpak)\b")),
    ("household_paper", re.compile(r"\b(toiletpapir|køkkenrulle|køkkenruller|husholdningspapir)\b")),
    ("milk", re.compile(r"\b(mælk|sødmælk|letmælk|minimælk|skummetmælk)\b")),
)
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
    canonical_family: str | None = None
    evidence: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    level: Literal["same_item", "compatible_variant", "probably_same", "not_same"]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    direct_price_comparison: bool = False
    left: ProductAnalysis
    right: ProductAnalysis
    evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


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


class ImageTextObservation(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)


class ImageEvidenceRequest(BaseModel):
    offer_name: str = Field(min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=100)
    raw_text: str | None = Field(default=None, max_length=1000)
    existing_variants: list[str] = Field(default_factory=list, max_length=30)
    observations: list[ImageTextObservation] = Field(min_length=1, max_length=100)


class RecognizedImageVariant(BaseModel):
    name: str
    confidence: float = Field(ge=0, le=1)
    match_level: Literal["same_item", "compatible_variant", "probably_same", "not_same"]
    explanation: str
    evidence: list[str] = Field(default_factory=list)


class ImageEvidenceResponse(BaseModel):
    ok: bool = True
    observed_text: str
    variants: list[RecognizedImageVariant]
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool = True


def store_path() -> Path:
    return Path(os.getenv("PRODUCT_IDENTITY_STORE_PATH", "/data/product-identity.json"))


def _fold(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in folded if not unicodedata.combining(character))


def normalize(value: str) -> str:
    value = value.replace("-", " ").replace("/", " ")
    return " ".join(TOKEN_RE.findall(_fold(value)))


def _canonical_family(normalized: str) -> str | None:
    return next(
        (family for family, pattern in CANONICAL_FAMILY_PATTERNS if pattern.search(normalized)),
        None,
    )


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
    types = sorted({
        *(TYPE_ALIASES[token] for token in tokens if token in TYPE_ALIASES),
        *(kind for kind, pattern in COMPOUND_TYPE_PATTERNS if pattern.search(normalized)),
    })
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
    canonical_family = store.get("families", {}).get(normalized) or _canonical_family(normalized)
    evidence: list[str] = []
    if brand:
        evidence.append(f"brand:{brand}")
    if canonical_family:
        evidence.append(f"family:{canonical_family}")
    evidence.extend(f"type:{value}" for value in types)
    evidence.extend(f"flavour:{value}" for value in flavours)
    if amount_text:
        evidence.append(f"amount:{amount_text}")
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
        canonical_family=canonical_family, evidence=evidence,
    )


def _product_overlap(left: ProductAnalysis, right: ProductAnalysis) -> float:
    left_tokens, right_tokens = set(left.product.split()), set(right.product.split())
    if not left_tokens or not right_tokens:
        return 0
    if left.canonical_id and left.canonical_id == right.canonical_id:
        return 1
    intersection = left_tokens & right_tokens
    if not intersection:
        if left.canonical_family and left.canonical_family == right.canonical_family:
            return 0.7
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

    def result(
        level: Literal["same_item", "compatible_variant", "probably_same", "not_same"],
        confidence: float,
        explanation: str,
        *,
        direct: bool = False,
        evidence: list[str] | None = None,
        conflicts: list[str] | None = None,
    ) -> MatchResult:
        return MatchResult(
            level=level, confidence=confidence, explanation=explanation,
            direct_price_comparison=direct, left=left, right=right,
            evidence=evidence or [], conflicts=conflicts or [],
        )

    store = _load_store()
    learned = store.get("matches", {}).get(_pair_key(left_text, right_text))
    if learned == "never_match":
        return result(
            "not_same", 1, "Match afvist af fælles produktviden.",
            conflicts=["learned:never_match"],
        )

    overlap = _product_overlap(left, right)
    if overlap == 0:
        return result(
            "not_same", .97, "Forskellige grundprodukter.",
            conflicts=["product-family"],
        )

    conflicting_types = bool(set(left.types) ^ set(right.types)) and bool(left.types or right.types)
    conflicting_flavours = bool(left.flavours and right.flavours and set(left.flavours) != set(right.flavours))
    if conflicting_types:
        if (set(left.types) ^ set(right.types)) <= {"light"}:
            return result(
                "compatible_variant", .92,
                "Samme produktfamilie, men almindelig og light/let må ikke behandles som samme vare.",
                evidence=[f"family:{left.canonical_family}"] if left.canonical_family else [],
                conflicts=["type:light"],
            )
        return result(
            "not_same", .96,
            "Afgørende type er forskellig, eksempelvis zero, light, økologisk eller fri-variant.",
            conflicts=[f"types:{','.join(left.types) or 'standard'}!={','.join(right.types) or 'standard'}"],
        )
    if conflicting_flavours:
        return result(
            "compatible_variant", .94,
            "Samme produktfamilie, men smagsvarianterne er forskellige.",
            conflicts=[f"flavours:{','.join(left.flavours)}!={','.join(right.flavours)}"],
        )

    if left.brand and right.brand and left.brand != right.brand:
        if left.canonical_family and left.canonical_family == right.canonical_family:
            return result(
                "compatible_variant", .93,
                "Samme produktfamilie, men forskellige mærker.",
                evidence=[f"family:{left.canonical_family}"],
                conflicts=[f"brand:{left.brand}!={right.brand}"],
            )
        return result(
            "not_same", .98, "Forskellige mærker.",
            conflicts=[f"brand:{left.brand}!={right.brand}"],
        )

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
        return result(
            "compatible_variant", .9,
            "Samme produktfamilie, men forskellig samlet pakkestørrelse.",
            conflicts=["amount"],
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
    evidence = []
    if left.canonical_id and left.canonical_id == right.canonical_id:
        evidence.append(f"canonical:{left.canonical_id}")
    if left.canonical_family and left.canonical_family == right.canonical_family:
        evidence.append(f"family:{left.canonical_family}")
    if left.brand and left.brand == right.brand:
        evidence.append(f"brand:{left.brand}")
    if compatible_amount:
        evidence.append("amount:compatible")
    return result(
        level, confidence, explanation,
        direct=level == "same_item" and (compatible_amount or (both_amounts_missing and left.normalized == right.normalized)),
        evidence=evidence,
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
            family = left.canonical_family or right.canonical_family
            if family:
                store.setdefault("families", {})[left.normalized] = family
                store.setdefault("families", {})[right.normalized] = family
        _save_store(store)
    return {"ok": True, "decision": request.decision}


IMAGE_PRICE_RE = re.compile(r"\b\d{1,4}(?:[,.]\d{1,2})?\s*(?:kr\.?|-)?\b", re.IGNORECASE)
IMAGE_NOISE_RE = re.compile(
    r"^(?:spar|tilbud|frit valg|kun|pr\.?\s*(?:stk|kg|pakke)|maks?\.?|gælder)\b",
    re.IGNORECASE,
)


def _clean_image_candidate(value: str) -> str:
    value = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:kg|g|ml|cl|l|stk|pk)\b", " ", value, flags=re.IGNORECASE)
    value = IMAGE_PRICE_RE.sub(" ", value)
    value = re.sub(r"\b(?:frit valg|udvalgte varianter|flere varianter)\b.*$", " ", value, flags=re.IGNORECASE)
    value = " ".join(value.strip(" .,:;*-/").split())
    return value.title() if value.isupper() else value


def interpret_image_evidence(request: ImageEvidenceRequest) -> ImageEvidenceResponse:
    """Turn local Apple Vision text into conservative product suggestions.

    The image never reaches QNAP. Candidates that contradict the advertised
    family are discarded, and every surviving result still requires a tap.
    """
    observations = [value for value in request.observations if value.confidence >= 0.35]
    observations.sort(key=lambda value: value.confidence, reverse=True)
    observed_text = " | ".join(dict.fromkeys(value.text for value in observations))
    offer_analysis = analyze(request.offer_name)
    shared_brand = normalize(request.brand or offer_analysis.brand or "") or None
    existing = {normalize(value) for value in request.existing_variants}
    candidates: dict[str, tuple[str, float, list[str]]] = {}

    for observation in observations:
        line = _clean_image_candidate(observation.text)
        if len(line) < 3 or IMAGE_NOISE_RE.search(line):
            continue
        extracted = extract_variants("image", line, None)
        names = [value.name for value in extracted] if len(extracted) > 1 else [line]
        for raw_name in names:
            name = _clean_image_candidate(raw_name)
            if len(name.split()) < 1 or len(name) > 100:
                continue
            analysis = analyze(name)
            if shared_brand and not analysis.brand:
                name = f"{shared_brand.title()} {name}"
                analysis = analyze(name)
            normalized_name = analysis.normalized
            if normalized_name in existing or normalized_name == offer_analysis.normalized:
                continue
            match = compare(request.offer_name, name)
            same_family = bool(
                offer_analysis.canonical_family
                and offer_analysis.canonical_family == analysis.canonical_family
            )
            same_brand = bool(offer_analysis.brand and offer_analysis.brand == analysis.brand)
            if match.level == "not_same" and not same_family and not same_brand:
                continue
            match_weight = {
                "same_item": 1.0,
                "compatible_variant": 0.92,
                "probably_same": 0.82,
                "not_same": 0.58,
            }[match.level]
            confidence = max(0.35, min(0.98, observation.confidence * match_weight))
            evidence = ["source:apple-vision", f"ocr:{observation.confidence:.2f}", *match.evidence]
            previous = candidates.get(normalized_name)
            if previous is None or confidence > previous[1]:
                candidates[normalized_name] = (name, confidence, evidence)

    variants: list[RecognizedImageVariant] = []
    for name, confidence, evidence in sorted(candidates.values(), key=lambda value: (-value[1], value[0].casefold()))[:12]:
        match = compare(request.offer_name, name)
        variants.append(RecognizedImageVariant(
            name=name,
            confidence=round(confidence, 3),
            match_level=match.level,
            explanation=match.explanation,
            evidence=evidence,
        ))
    overall = sum(value.confidence for value in variants) / len(variants) if variants else 0.0
    return ImageEvidenceResponse(
        observed_text=observed_text,
        variants=variants,
        confidence=round(overall, 3),
        requires_confirmation=True,
    )


@router.post("/image-evidence", response_model=ImageEvidenceResponse)
async def image_evidence(request: ImageEvidenceRequest) -> ImageEvidenceResponse:
    return interpret_image_evidence(request)


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

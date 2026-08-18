from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .meny_flyer import Offer


SPACE_RE = re.compile(r"\s+")
BOILERPLATE_RE = re.compile(
    r"\b(frit\s+valg|udvalgte\s+varianter|flere\s+varianter|maks?\.?\s*\d+|"
    r"kg[- ]?pris|literpris|pr\.?\s*(?:stk|kg|liter|pakke))\b.*$",
    re.IGNORECASE,
)
MEASURE_ONLY_RE = re.compile(
    r"^[\d\s.,x×%+\-/]+(?:g|kg|ml|cl|l|liter|stk|pk|pakker?)?\.?$",
    re.IGNORECASE,
)
_FEEDBACK_CACHE_TTL_SECONDS = 1.0
_feedback_cache_lock = threading.RLock()
_feedback_cache: dict[Path, tuple[float, tuple[int, int] | None, dict]] = {}


class HotspotBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source: str


class OCRTextRegion(BaseModel):
    text: str
    box: HotspotBox
    confidence: float = Field(ge=0, le=1)
    role: str = "text"


class VariantCandidate(BaseModel):
    id: str
    name: str
    confidence: float = Field(ge=0, le=1)
    source: str


class QualityAssessment(BaseModel):
    score: float = Field(ge=0, le=1)
    hotspot_confidence: float = Field(ge=0, le=1)
    variant_confidence: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class LearningAdjustment:
    score: float = 0.0
    position_reports: int = 0
    variant_reports: int = 0


def _space(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def box_from_mapping(value: object, *, source: str = "native") -> HotspotBox | None:
    """Normalize known rectangle schemas and reject geometry outside the page.

    The source APIs use both percentages and normalized coordinates. A small
    overshoot is clamped because several readers round their right/bottom edge
    to 100.01%, while materially invalid rectangles are rejected.
    """
    if not isinstance(value, dict):
        return None
    candidates = [value]
    candidates.extend(
        child for key in ("bounds", "rect", "rectangle", "position")
        if isinstance((child := value.get(key)), dict)
    )
    for candidate in candidates:
        x = _number(candidate.get("x", candidate.get("left")))
        y = _number(candidate.get("y", candidate.get("top")))
        width = _number(candidate.get("width", candidate.get("w")))
        height = _number(candidate.get("height", candidate.get("h")))
        if None in (x, y, width, height) or width <= 0 or height <= 0:
            continue
        # Values up to 1.01 are treated as normalized coordinates so harmless
        # provider rounding (1.0001) cannot accidentally shrink a box by 100.
        # Larger values identify a percentage-based schema consistently.
        if max(x, y, width, height) > 1.01:
            if max(x, y, width, height) > 100.5:
                continue
            x, y, width, height = x / 100, y / 100, width / 100, height / 100
        if x < -0.01 or y < -0.01 or x >= 1.01 or y >= 1.01:
            continue
        x, y = max(0.0, x), max(0.0, y)
        # Only tiny edge overshoots are safe to clamp. A rectangle extending
        # materially outside the page is a scale/coordinate error and must not
        # drive either a '+' marker or image recognition crop.
        if x + width > 1.015 or y + height > 1.015:
            continue
        width, height = min(width, 1 - x), min(height, 1 - y)
        if width < 0.008 or height < 0.008:
            continue
        area = width * height
        if area > 0.92:
            continue
        confidence = 0.97 if source in {"native", "tjek-polygon", "ipaper-marker"} else 0.82
        if area > 0.65:
            confidence -= 0.25
        elif area < 0.0015:
            confidence -= 0.12
        return HotspotBox(
            x=x, y=y, width=width, height=height,
            confidence=max(0.35, confidence), source=source,
        )
    return None


def box_from_polygon(
    points: Iterable[Sequence[object]],
    *,
    vertical_scale: float = 1.0,
    source: str = "native-polygon",
) -> HotspotBox | None:
    """Build a recall-first bounding box from provider polygon coordinates."""
    if vertical_scale <= 0 or vertical_scale != vertical_scale:
        return None

    parsed: list[tuple[float, float]] = []
    for point in points:
        if len(point) < 2:
            continue
        x = _number(point[0])
        y = _number(point[1])
        if x is not None and y is not None:
            parsed.append((x, y / vertical_scale))

    if len(parsed) < 2:
        return None

    xs, ys = zip(*parsed)
    x = max(0.0, min(1.0, min(xs)))
    y = max(0.0, min(1.0, min(ys)))
    width = max(0.0001, min(1.0 - x, max(xs) - min(xs)))
    height = max(0.0001, min(1.0 - y, max(ys) - min(ys)))
    if width <= 0 or height <= 0:
        return None

    area = width * height
    confidence = 0.97 if source in {"native", "tjek-polygon", "ipaper-marker"} else 0.82
    if area > 0.65:
        confidence -= 0.25
    elif area < 0.0015:
        confidence -= 0.12

    return HotspotBox(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=max(0.35, confidence),
        source=source,
    )


def union_boxes(boxes: Iterable[HotspotBox]) -> HotspotBox | None:
    values = list(boxes)
    if not values:
        return None
    x, y = min(box.x for box in values), min(box.y for box in values)
    right = max(box.x + box.width for box in values)
    bottom = max(box.y + box.height for box in values)
    result = box_from_mapping(
        {"x": x, "y": y, "width": right - x, "height": bottom - y},
        source="coupled-native",
    )
    if result is None:
        return None
    return result.model_copy(update={"confidence": min(box.confidence for box in values)})


def intersection_over_union(left: HotspotBox, right: HotspotBox) -> float:
    x1, y1 = max(left.x, right.x), max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union > 0 else 0.0


def extract_ocr_regions(payload: object) -> list[OCRTextRegion]:
    """Read positioned OCR/text blocks from common provider schemas.

    Kurv does not infer coordinates from a flat page transcript. Only blocks
    carrying real geometry are accepted, preventing text from a neighbouring
    advert from leaking into the selected hotspot.
    """
    rows: list[dict] = []
    if isinstance(payload, dict):
        for key in ("ocr", "ocr_blocks", "text_blocks", "textBlocks", "regions"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend(row for row in value if isinstance(row, dict))
            elif isinstance(value, dict):
                nested = value.get("blocks") or value.get("regions") or value.get("items")
                if isinstance(nested, list):
                    rows.extend(row for row in nested if isinstance(row, dict))
    regions: list[OCRTextRegion] = []
    for row in rows:
        text = _space(row.get("text") or row.get("value") or row.get("label"))
        box = box_from_mapping(row, source="ocr-text-region")
        if not text or box is None:
            continue
        raw_confidence = _number(row.get("confidence"))
        if raw_confidence is None:
            confidence = 0.72
        else:
            confidence = raw_confidence / 100 if raw_confidence > 1 else raw_confidence
        regions.append(OCRTextRegion(
            text=text,
            box=box,
            confidence=max(0.0, min(1.0, confidence)),
            role=_space(row.get("role") or row.get("type") or "text").casefold(),
        ))
    return sorted(regions, key=lambda value: (value.box.y, value.box.x))


def text_for_hotspot(regions: Sequence[OCRTextRegion], hotspot: HotspotBox) -> str:
    """Return only OCR blocks overlapping or immediately touching a hotspot."""
    expanded = HotspotBox(
        x=max(0.0, hotspot.x - 0.035),
        y=max(0.0, hotspot.y - 0.035),
        width=min(1.0 - max(0.0, hotspot.x - 0.035), hotspot.width + 0.07),
        height=min(1.0 - max(0.0, hotspot.y - 0.035), hotspot.height + 0.07),
        confidence=hotspot.confidence,
        source=hotspot.source,
    )
    matched = [
        region.text for region in regions
        if region.confidence >= 0.45 and intersection_over_union(expanded, region.box) > 0
    ]
    return _space(" ".join(dict.fromkeys(matched)))


def _explicit_names(value: object) -> list[str]:
    names: list[str] = []

    def visit(node: object, *, active: bool = False) -> None:
        if isinstance(node, str):
            if active and 2 <= len(_space(node)) <= 120:
                names.append(_space(node).strip(" *"))
            return
        if isinstance(node, list):
            for child in node:
                visit(child, active=active)
            return
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            lowered = str(key).casefold()
            child_active = active or lowered in {
                "variants", "variant", "products", "product_variants",
                "choices", "alternatives", "items",
            }
            if child_active and lowered in {"name", "title", "label", "heading"}:
                visit(child, active=True)
            elif lowered in {
                "variants", "variant", "products", "product_variants",
                "choices", "alternatives", "items",
            }:
                visit(child, active=True)

    visit(value)
    return list(dict.fromkeys(filter(None, names)))


def _restore_variant_context(names: list[str]) -> list[str]:
    if len(names) < 2:
        return names
    first = names[0]
    folded = first.casefold()
    roots = ("kalkun", "kylling", "svine", "okse", "lamme", "kalve")
    root = next((root for root in roots if folded.startswith(root)), None)
    if root:
        original = first[:len(root)]
        names = [
            original + name.lstrip("-–— ") if index and name.startswith(("-", "–", "—")) else name
            for index, name in enumerate(names)
        ]
    shared = re.split(r"\s+(?:i|med|uden)\s+", names[0], maxsplit=1, flags=re.IGNORECASE)[0]
    names = [
        f"{shared} {name}" if index and re.match(r"^(?:i|med|uden)\b", name, re.IGNORECASE) else name
        for index, name in enumerate(names)
    ]
    if " " in names[0]:
        prefix = names[0].rsplit(" ", 1)[0]
        names = [name if index == 0 or " " in name else f"{prefix} {name}" for index, name in enumerate(names)]
    return names


def extract_variants(
    identity: str,
    heading: str,
    description: str | None = None,
    *,
    payload: object = None,
) -> list[VariantCandidate]:
    """Use Kurv's text/structure-only Variant Extractor v2 as the normal path."""
    # Lazy import avoids a module cycle: variant_extractor_v2 uses the shared
    # VariantCandidate model defined above.
    from .variant_extractor_v2 import extract_variants_v2

    return extract_variants_v2(
        identity,
        heading,
        description,
        payload=payload,
    )


def assess_quality(
    *,
    heading: str,
    raw_text: str,
    price: float | None,
    box: HotspotBox | None,
    variants: Sequence[VariantCandidate] | Sequence[str],
    structured: bool,
    has_crop: bool,
) -> QualityAssessment:
    issues: list[str] = []
    signals: list[str] = []
    score = 0.10
    if _space(heading):
        score += 0.16
        signals.append("product-heading")
    else:
        issues.append("missing-heading")
    if price is not None and price >= 0:
        score += 0.10
        signals.append("price")
    else:
        issues.append("missing-price")
    if box is not None:
        score += 0.30 * box.confidence
        signals.append(box.source)
    else:
        issues.append("missing-hotspot")
    if structured:
        score += 0.15
        signals.append("structured-source")
    if has_crop:
        score += 0.06
        signals.append("offer-crop")
    if _space(raw_text):
        score += 0.05
        signals.append("text-layer")

    variant_confidences = [
        variant.confidence if isinstance(variant, VariantCandidate) else 0.65
        for variant in variants
    ]
    variant_confidence = min(variant_confidences, default=0.35)
    if variants:
        score += 0.08 * variant_confidence
        signals.append("variant-data")
    else:
        issues.append("missing-variants")
    if variant_confidence < 0.70:
        issues.append("uncertain-variants")

    score = max(0.0, min(1.0, score))
    hotspot_confidence = box.confidence if box is not None else 0.0
    if score < 0.60:
        issues.append("manual-review-recommended")
    return QualityAssessment(
        score=round(score, 3),
        hotspot_confidence=round(hotspot_confidence, 3),
        variant_confidence=round(variant_confidence, 3),
        issues=list(dict.fromkeys(issues)),
        signals=list(dict.fromkeys(signals)),
    )


def _offer_box(offer: "Offer") -> HotspotBox | None:
    return box_from_mapping({
        "x": offer.hotspot_x, "y": offer.hotspot_y,
        "width": offer.hotspot_width, "height": offer.hotspot_height,
    }, source=offer.quality_source or "native")


def couple_offers(offers: Sequence["Offer"]) -> list["Offer"]:
    """Merge only near-identical duplicate source rows for one visual offer."""
    result: list["Offer"] = []
    for offer in offers:
        box = _offer_box(offer)
        match_index: int | None = None
        for index, existing in enumerate(result):
            if existing.page_number != offer.page_number or existing.price != offer.price:
                continue
            existing_box = _offer_box(existing)
            same_label = _space(existing.product_name).casefold() == _space(offer.product_name).casefold()
            if same_label and (
                existing_box is None
                or box is None
                or intersection_over_union(existing_box, box) >= 0.90
            ):
                match_index = index
                break
        if match_index is None:
            result.append(offer)
            continue
        existing = result[match_index]
        variants = list(existing.variants)
        seen = {variant.name.casefold() for variant in variants}
        for variant in offer.variants:
            key = variant.name.casefold()
            if key not in seen:
                variants.append(variant)
                seen.add(key)
        existing_box = _offer_box(existing)
        union = union_boxes(value for value in (existing_box, box) if value is not None)
        updates = {
            "variants": variants,
            "raw_text": " | ".join(dict.fromkeys(filter(None, (existing.raw_text, offer.raw_text)))),
            "quality_score": max(existing.quality_score, offer.quality_score),
            "variant_confidence": max(existing.variant_confidence, offer.variant_confidence),
            "quality_issues": list(dict.fromkeys([*existing.quality_issues, *offer.quality_issues])),
            "quality_signals": list(dict.fromkeys([*existing.quality_signals, *offer.quality_signals, "coupled-source-rows"])),
        }
        if union is not None:
            updates.update({
                "hotspot_x": union.x, "hotspot_y": union.y,
                "hotspot_width": union.width, "hotspot_height": union.height,
                "hotspot_confidence": union.confidence,
            })
        result[match_index] = existing.model_copy(update=updates)
    return sorted(result, key=lambda value: (value.page_number or 0, value.hotspot_y or 0, value.hotspot_x or 0))


def feedback_key(retailer: str, quality_source: str, publication_id: str | None = None) -> str:
    key = f"{_space(retailer).casefold()}|{_space(quality_source).casefold() or 'unknown'}"
    publication = _space(publication_id).casefold()
    return f"{key}|{publication}" if publication else key


def _empty_feedback_store() -> dict:
    return {"version": 2, "sources": {}, "publications": {}, "reports": []}


def _normalize_feedback_store(payload: object) -> dict:
    if not isinstance(payload, dict):
        return _empty_feedback_store()
    result = deepcopy(payload)
    result["version"] = 2
    for key, default in (("sources", {}), ("publications", {}), ("reports", [])):
        if not isinstance(result.get(key), type(default)):
            result[key] = default
        else:
            result.setdefault(key, default)
    return result


def _feedback_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def clear_feedback_store_cache(path: str | Path | None = None) -> None:
    with _feedback_cache_lock:
        if path is None:
            _feedback_cache.clear()
        else:
            _feedback_cache.pop(Path(path), None)


def load_feedback_store(path: str | Path) -> dict:
    file_path = Path(path)
    now = time.monotonic()
    with _feedback_cache_lock:
        cached = _feedback_cache.get(file_path)
        if cached is not None and now - cached[0] < _FEEDBACK_CACHE_TTL_SECONDS:
            return deepcopy(cached[2])
        signature = _feedback_signature(file_path)
        if cached is not None and cached[1] == signature:
            _feedback_cache[file_path] = (now, signature, cached[2])
            return deepcopy(cached[2])
        if signature is None:
            payload = _empty_feedback_store()
        else:
            try:
                payload = _normalize_feedback_store(json.loads(file_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                payload = _empty_feedback_store()
        _feedback_cache[file_path] = (now, signature, payload)
        return deepcopy(payload)


def save_feedback_store(path: str | Path, payload: dict) -> None:
    file_path = Path(path)
    normalized = _normalize_feedback_store(payload)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(file_path)
    signature = _feedback_signature(file_path)
    with _feedback_cache_lock:
        _feedback_cache[file_path] = (time.monotonic(), signature, deepcopy(normalized))


def _learning_score(row: dict) -> float:
    correct = int(row.get("correct", 0))
    wrong_position = int(row.get("wrong_position", 0))
    wrong_variants = int(row.get("wrong_variants", 0))
    total = correct + wrong_position + wrong_variants
    # Bayesian prior prevents one report from radically changing all markers
    # from a retailer/source combination.
    return ((correct + 4) / (total + 8) - 0.5) * 0.16 if total else 0.0


def learned_adjustment(
    store: dict,
    retailer: str,
    quality_source: str,
    publication_id: str | None = None,
) -> LearningAdjustment:
    source_row = store.get("sources", {}).get(feedback_key(retailer, quality_source), {})
    scoped_row = store.get("publications", {}).get(
        feedback_key(retailer, quality_source, publication_id), {},
    ) if publication_id else {}
    source_score = _learning_score(source_row)
    scoped_score = _learning_score(scoped_row)
    # A bad marker in one weekly edition must never lower confidence in every
    # future edition. Cross-publication learning may provide a small positive
    # prior; negative corrections stay bound to the concrete publication.
    positive_source_score = max(0.0, source_score)
    score = positive_source_score * 0.35
    if scoped_row:
        score = positive_source_score * 0.25 + scoped_score * 0.75
    count_row = scoped_row if publication_id else source_row
    return LearningAdjustment(
        score=max(-0.08, min(0.08, score)),
        position_reports=int(count_row.get("wrong_position", 0)),
        variant_reports=int(count_row.get("wrong_variants", 0)),
    )


def stable_report_id(publication_id: str, offer_id: str, decision: str, created_at: int) -> str:
    return hashlib.sha256(f"{publication_id}|{offer_id}|{decision}|{created_at}".encode()).hexdigest()[:20]

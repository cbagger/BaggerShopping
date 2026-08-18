from __future__ import annotations

import re
from typing import Iterable

from .flyer_intelligence import VariantCandidate


SPACE_RE = re.compile(r"\s+")
CHOICE_KEYS = {
    "variants",
    "variant",
    "products",
    "product_variants",
    "productvariants",
    "choices",
    "alternatives",
}
GENERIC_ITEM_KEYS = {"items"}
NAME_KEYS = {"name", "title", "label", "heading", "product_name", "productname"}
PRODUCT_ID_KEYS = {
    "id",
    "product_id",
    "productid",
    "sku",
    "ean",
    "gtin",
    "article_id",
    "articleid",
}
IGNORED_BRANCH_KEYS = {
    "image",
    "images",
    "image_labels",
    "imagelabels",
    "vision",
    "vision_results",
    "recognition",
    "detections",
    "ocr",
    "ocr_blocks",
    "text_blocks",
    "textblocks",
    "regions",
    "crop",
    "crops",
}
DESCRIPTION_KEYS = ("description", "desc", "subtitle")
CHOICE_SEPARATOR_RE = re.compile(r"\s*,\s*|\s*;\s*|\s+eller\s+|\s+/\s+", re.IGNORECASE)
EXPLICIT_CHOICE_RE = re.compile(r"(?:\s+eller\s+|\s+/\s+|[,;])", re.IGNORECASE)
LEADING_CHOICE_RE = re.compile(
    r"^(?:frit\s+valg(?:\s+mellem)?|vælg\s+(?:mellem|imellem)|udvalgte\s+varianter\s*:?)\s+",
    re.IGNORECASE,
)
MEASURE_SUFFIX_RE = re.compile(
    r"(?:[,;\s-]+)(?:ca\.?\s*)?\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:g|kg|mg|ml|cl|dl|l|liter|stk\.?|styk(?:ker)?|pk\.?|pakker?)\b.*$",
    re.IGNORECASE,
)
PRICE_SUFFIX_RE = re.compile(
    r"(?:[,;\s-]+)\d+(?:[.,]\d+)?\s*(?:kr\.?|,-)\b.*$",
    re.IGNORECASE,
)
PROMO_TAIL_RE = re.compile(
    r"\s+(?:frit\s+valg|udvalgte\s+varianter|flere\s+varianter|assorteret|"
    r"maks?\.?\s*\d+|kg[- ]?pris|literpris|pr\.?\s*(?:stk|kg|liter|pakke))\b.*$",
    re.IGNORECASE,
)
PERCENT_SUFFIX_RE = re.compile(r"(?:[,;\s-]+)\d+(?:[.,]\d+)?\s*%\b.*$", re.IGNORECASE)
MEASURE_ONLY_RE = re.compile(
    r"^[\d\s.,x×%+\-/]+(?:g|kg|mg|ml|cl|dl|l|liter|stk|pk|pakker?|kr)?\.?$",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"\b(?:ingredienser?|næringsindhold|energi|protein|kulhydrat|fedt\s+pr\.|"
    r"opbevaring|tilberedning|serveringsforslag|se\s+mere|læs\s+mere|www\.|https?://)\b",
    re.IGNORECASE,
)
GENERIC_ONLY_RE = re.compile(
    r"^(?:frit\s+valg|udvalgte\s+varianter|flere\s+varianter|assorteret|"
    r"se\s+udvalget|tilbud|kampagne)$",
    re.IGNORECASE,
)


def _space(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9æøå]+", " ", value.casefold()).strip()


def _strip_repeated_suffixes(value: str, *, preserve_measure: bool) -> str:
    previous = None
    result = value
    while previous != result:
        previous = result
        result = PRICE_SUFFIX_RE.sub("", result)
        if not preserve_measure:
            result = MEASURE_SUFFIX_RE.sub("", result)
        result = PERCENT_SUFFIX_RE.sub("", result)
        result = PROMO_TAIL_RE.sub("", result)
    return result


def _clean_name(
    value: object,
    *,
    campaign_heading: str = "",
    preserve_measure: bool = False,
) -> str | None:
    name = _space(value).strip("•·*|,:;–— ")
    if not name:
        return None

    heading_key = _canonical(campaign_heading)
    parenthetical = re.search(r"\s*\(([^()]*)\)\s*$", name)
    if parenthetical and heading_key and _canonical(parenthetical.group(1)) == heading_key:
        name = name[: parenthetical.start()].strip()

    name = _strip_repeated_suffixes(name, preserve_measure=preserve_measure).strip("•·*|,:;–— ")
    if not name or len(name) < 2 or len(name) > 120:
        return None
    if MEASURE_ONLY_RE.fullmatch(name) or GENERIC_ONLY_RE.fullmatch(name):
        return None
    if NOISE_RE.search(name):
        return None
    if len(name.split()) > 14:
        return None
    if name.count(".") >= 3 or name.count(":") >= 2:
        return None
    return _space(name)


def _dedupe_clean(
    values: Iterable[object],
    *,
    campaign_heading: str,
    preserve_measure: bool,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_name(
            value,
            campaign_heading=campaign_heading,
            preserve_measure=preserve_measure,
        )
        if not cleaned:
            continue
        key = _canonical(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _product_like_items(node: object, *, campaign_heading: str) -> list[str]:
    """Accept a generic provider ``items`` collection only when it is clearly products.

    This intentionally keeps the v2 false-positive protection: arbitrary item
    lists are ignored. A generic collection becomes usable only when at least
    two rows carry a product-like identifier and a clean product name. That
    recovers providers which expose variants as ``items`` without trusting OCR,
    nutrition rows or UI metadata.
    """
    if not isinstance(node, list):
        return []
    names: list[str] = []
    for child in node[:40]:
        if not isinstance(child, dict):
            continue
        lowered = {str(key).casefold(): value for key, value in child.items()}
        if not any(key in lowered and _space(lowered[key]) for key in PRODUCT_ID_KEYS):
            continue
        raw_name = next(
            (
                lowered[key]
                for key in NAME_KEYS
                if key in lowered and isinstance(lowered[key], (str, int, float))
            ),
            None,
        )
        cleaned = _clean_name(
            raw_name,
            campaign_heading=campaign_heading,
            preserve_measure=True,
        )
        if cleaned:
            names.append(cleaned)
    return _dedupe_clean(
        names,
        campaign_heading=campaign_heading,
        preserve_measure=True,
    ) if len(names) >= 2 else []


def _structured_names(payload: object, *, campaign_heading: str) -> list[str]:
    raw_names: list[object] = []
    generic_names: list[str] = []

    def collect_choice_node(node: object) -> None:
        if isinstance(node, str):
            raw_names.append(node)
            return
        if isinstance(node, list):
            for child in node:
                collect_choice_node(child)
            return
        if not isinstance(node, dict):
            return

        for key, child in node.items():
            lowered = str(key).casefold()
            if lowered in IGNORED_BRANCH_KEYS:
                continue
            if lowered in NAME_KEYS and isinstance(child, (str, int, float)):
                raw_names.append(child)
        for key, child in node.items():
            lowered = str(key).casefold()
            if lowered in IGNORED_BRANCH_KEYS:
                continue
            if lowered in CHOICE_KEYS:
                collect_choice_node(child)

    def find_choice_containers(node: object) -> None:
        if isinstance(node, list):
            for child in node:
                find_choice_containers(child)
            return
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            lowered = str(key).casefold()
            if lowered in IGNORED_BRANCH_KEYS:
                continue
            if lowered in CHOICE_KEYS:
                collect_choice_node(child)
            elif lowered in GENERIC_ITEM_KEYS:
                generic_names.extend(
                    _product_like_items(child, campaign_heading=campaign_heading)
                )
            elif isinstance(child, (dict, list)):
                find_choice_containers(child)

    find_choice_containers(payload)
    explicit = _dedupe_clean(
        raw_names,
        campaign_heading=campaign_heading,
        preserve_measure=True,
    )
    if explicit:
        return explicit
    return _dedupe_clean(
        generic_names,
        campaign_heading=campaign_heading,
        preserve_measure=True,
    )


def _provider_description(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in DESCRIPTION_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and _space(value):
            return _space(value)
    return None


def _restore_variant_context(names: list[str]) -> list[str]:
    if len(names) < 2:
        return names

    first = names[0]
    if first.endswith("-"):
        first_without_hyphen = first[:-1].rstrip()
        for ending in ("over", "under", "inder", "yder"):
            if first_without_hyphen.casefold().endswith(ending):
                shared_stem = first_without_hyphen[: -len(ending)]
                names[0] = first_without_hyphen
                names = [
                    value if index == 0 or " " in value or value.startswith(("-", "–", "—"))
                    else f"{shared_stem}{value}"
                    for index, value in enumerate(names)
                ]
                break

    first = names[0]
    folded = first.casefold()
    roots = ("kalkun", "kylling", "svine", "okse", "lamme", "kalve")
    root = next((value for value in roots if folded.startswith(value)), None)
    if root:
        original_root = first[: len(root)]
        names = [
            original_root + name.lstrip("-–— ")
            if index and name.startswith(("-", "–", "—"))
            else name
            for index, name in enumerate(names)
        ]

    shared = re.split(r"\s+(?:i|med|uden)\s+", names[0], maxsplit=1, flags=re.IGNORECASE)[0]
    names = [
        f"{shared} {name}"
        if index and re.match(r"^(?:i|med|uden)\b", name, re.IGNORECASE)
        else name
        for index, name in enumerate(names)
    ]

    if " " in names[0]:
        prefix = names[0].rsplit(" ", 1)[0]
        restored = [names[0]]
        for name in names[1:]:
            if " " not in name and name[:1].islower():
                restored.append(f"{prefix} {name}")
            else:
                restored.append(name)
        names = restored

    return names


def _split_choices(text: str, *, campaign_heading: str) -> list[str]:
    value = _space(text)
    if not value or not EXPLICIT_CHOICE_RE.search(value):
        return []

    value = LEADING_CHOICE_RE.sub("", value)
    pieces = CHOICE_SEPARATOR_RE.split(value)
    names = _dedupe_clean(
        pieces,
        campaign_heading=campaign_heading,
        preserve_measure=True,
    )
    if not 2 <= len(names) <= 12:
        return []
    names = _restore_variant_context(names)
    return _dedupe_clean(
        names,
        campaign_heading=campaign_heading,
        preserve_measure=True,
    )


def _description_choices(description: str | None, *, campaign_heading: str) -> list[str]:
    value = _space(description)
    if not value or NOISE_RE.search(value):
        return []
    clause = value.split(".", 1)[0]
    if len(clause) > 320:
        return []
    return _split_choices(clause, campaign_heading=campaign_heading)


def extract_variants_v3(
    identity: str,
    heading: str,
    description: str | None = None,
    *,
    payload: object = None,
) -> list[VariantCandidate]:
    """Recall-safe provider/text variant extraction for Kurv.

    Variant Intelligence v3 keeps the fail-closed boundary around OCR/vision,
    but no longer throws away trustworthy product pack/size facts. Explicit
    provider choices win, then explicit heading/description alternatives. A
    generic ``items`` collection is accepted only when it contains at least two
    clean product rows with real product identifiers.

    Low-confidence alternatives are returned rather than hidden. The iOS choice
    state remains responsible for requiring a user selection instead of direct
    add when evidence is not strong enough.
    """
    campaign_heading = _space(heading)
    clean_heading = _clean_name(
        campaign_heading,
        campaign_heading="",
        preserve_measure=False,
    ) or campaign_heading

    structured = _structured_names(payload, campaign_heading=campaign_heading)
    heading_choices = _split_choices(campaign_heading, campaign_heading=campaign_heading)
    trusted_description = _provider_description(payload) if isinstance(payload, dict) else description
    description_choices = _description_choices(
        trusted_description,
        campaign_heading=campaign_heading,
    )

    if len(structured) > 1:
        names, source, confidence = structured, "structured-products-v3", 0.99
    elif len(heading_choices) > 1:
        names, source, confidence = heading_choices, "heading-v3", 0.93
    elif len(description_choices) > 1:
        names, source, confidence = description_choices, "description-text-v3", 0.82
    elif len(structured) == 1:
        names, source, confidence = structured, "structured-products-v3", 0.97
    else:
        names, source, confidence = [clean_heading], "campaign-heading-v3", 0.62

    names = _dedupe_clean(
        names,
        campaign_heading=campaign_heading,
        preserve_measure=source != "campaign-heading-v3",
    )
    if not names:
        names = [clean_heading or campaign_heading]
        source, confidence = "campaign-heading-v3", 0.55

    return [
        VariantCandidate(
            id=f"{identity}-{index}",
            name=name,
            confidence=confidence,
            source=source,
        )
        for index, name in enumerate(names[:12])
    ]

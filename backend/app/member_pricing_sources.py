from __future__ import annotations

import re
from typing import Iterable

from .meny_flyer import Offer, Publication, _normalize_space


_SKIP_STRUCTURED_KEYS = {
    "id", "url", "source", "image", "images", "zoom", "view", "thumbnail",
    "location", "locations", "bounds", "position", "polygon", "quantity", "size",
    "unit", "ocr", "ocrblocks", "ocr_blocks", "textblocks", "text_blocks", "regions",
}


def _key_words(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ").replace("-", " ")
    value = _normalize_space(value).casefold()
    if value in {"cpoffer", "cp offer", "customer programme offer", "customer program offer"}:
        return "member price"
    return value


def _structured_context(value: object, path: tuple[str, ...] = ()) -> list[str]:
    """Expose provider text/member-price metadata without geometry or weights."""
    if isinstance(value, dict):
        result: list[str] = []
        for raw_key, child in value.items():
            key = _key_words(str(raw_key))
            compact_key = key.replace(" ", "")
            if compact_key in _SKIP_STRUCTURED_KEYS or key in _SKIP_STRUCTURED_KEYS:
                continue
            result.extend(_structured_context(child, (*path, key)))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value[:80]:
            result.extend(_structured_context(child, path))
        return result
    if isinstance(value, str):
        text = _normalize_space(value)
        if not text or text.startswith(("http://", "https://")) or len(text) > 1800:
            return []
        prefix = " ".join(path[-2:])
        return [_normalize_space(f"{prefix} {text}") if prefix else text]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return []
    joined = " ".join(path).casefold()
    memberish = any(token in joined for token in ("member", "plus", "club", "klub", "app price", "kundeklub", "cp offer"))
    generic_pre = any(token in joined for token in ("pre price", "preprice", "unit price", "kg price", "liter price"))
    if not memberish or generic_pre:
        return []
    # Add a currency suffix so whole-number structured values such as 25 are
    # unambiguously prices rather than package sizes or campaign limits.
    return [_normalize_space(f"{joined} {value} kr")]


def _significant_needles(offer: Offer) -> list[str]:
    values = [offer.product_name, *(variant.name for variant in offer.variants)]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_space(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
    return result


def _localized_context(text: str, needles: Iterable[str], radius: int = 320) -> str:
    compact = _normalize_space(text)
    if not compact:
        return ""
    folded = compact.casefold()
    positions: list[tuple[int, int]] = []
    needle_values = list(needles)
    for needle in needle_values:
        normalized = _normalize_space(needle)
        if not normalized:
            continue
        index = folded.find(normalized.casefold())
        if index >= 0:
            positions.append((index, len(normalized)))
    if not positions:
        tokens = sorted(
            {
                token.casefold()
                for needle in needle_values
                for token in re.findall(r"[A-Za-zÆØÅæøå0-9+]+", needle)
                if len(token) >= 4
            },
            key=len,
            reverse=True,
        )
        for token in tokens:
            index = folded.find(token)
            if index >= 0:
                positions.append((index, len(token)))
                break
    if not positions:
        return ""
    start, length = min(positions, key=lambda value: value[0])
    left = max(0, start - radius)
    right = min(len(compact), start + length + radius)
    return compact[left:right]


def _append_context(offer: Offer, parts: Iterable[str]) -> Offer:
    values = [offer.raw_text]
    values.extend(_normalize_space(value) for value in parts if _normalize_space(value))
    merged = " | ".join(dict.fromkeys(value for value in values if value))
    if merged == offer.raw_text:
        return offer
    return offer.model_copy(update={"raw_text": merged})


def enrich_ipaper_offers(publication: Publication, offers: list[Offer]) -> list[Offer]:
    result: list[Offer] = []
    for offer in offers:
        context = ""
        if offer.page_number is not None and 0 < offer.page_number <= len(publication.page_texts):
            context = _localized_context(publication.page_texts[offer.page_number - 1], _significant_needles(offer))
        result.append(_append_context(offer, [context]))
    return result


def enrich_tjek_offers(offers: list[Offer], hotspot_rows: object, detailed_rows: object = None) -> list[Offer]:
    details = {
        str(row.get("id")): row
        for row in (detailed_rows if isinstance(detailed_rows, list) else [])
        if isinstance(row, dict) and row.get("id")
    }
    hotspots = {
        str((row.get("offer") if isinstance(row.get("offer"), dict) else row).get("id")): row
        for row in (hotspot_rows if isinstance(hotspot_rows, list) else [])
        if isinstance(row, dict)
        and (row.get("offer") if isinstance(row.get("offer"), dict) else row).get("id")
    }
    result: list[Offer] = []
    for offer in offers:
        source_id: str | None = None
        page_suffix = f"-{offer.page_number}" if offer.page_number is not None else ""
        if page_suffix and offer.id.endswith(page_suffix):
            source_id = offer.id[:-len(page_suffix)]
        if source_id not in details and source_id not in hotspots:
            source_id = next(
                (row_id for row_id, row in details.items()
                 if _normalize_space(str(row.get("heading") or "")).rstrip("*").casefold()
                 == _normalize_space(offer.product_name).casefold()),
                source_id,
            )
        parts: list[str] = []
        if source_id and source_id in details:
            parts.extend(_structured_context(details[source_id]))
        if source_id and source_id in hotspots:
            hotspot_payload = hotspots[source_id].get("offer") if isinstance(hotspots[source_id].get("offer"), dict) else hotspots[source_id]
            parts.extend(_structured_context(hotspot_payload))
        result.append(_append_context(offer, parts))
    return result


def enrich_schwarz_publication(publication: Publication, payload: object) -> Publication:
    if not isinstance(payload, dict):
        return publication
    flyer = payload.get("flyer") if isinstance(payload.get("flyer"), dict) else {}
    pages = flyer.get("pages") if isinstance(flyer.get("pages"), list) else []
    raw_products = flyer.get("products")
    if isinstance(raw_products, dict):
        products = {str(key): value for key, value in raw_products.items() if isinstance(value, dict)}
    elif isinstance(raw_products, list):
        products = {
            str(product.get("id")): product
            for product in raw_products
            if isinstance(product, dict) and product.get("id")
        }
    else:
        products = {}

    enriched: list[Offer] = []
    for offer in publication.structured_offers:
        parts: list[str] = []
        if offer.id in products:
            parts.extend(_structured_context(products[offer.id]))
        if offer.page_number is not None and 0 < offer.page_number <= len(pages):
            page = pages[offer.page_number - 1]
            if isinstance(page, dict):
                page_text = " ".join(str(page.get(key) or "") for key in ("keyWords", "keywords", "altText", "alt_text"))
                context = _localized_context(page_text, _significant_needles(offer), radius=380)
                if context:
                    parts.append(context)
                for link in page.get("links") or []:
                    if isinstance(link, dict) and str(link.get("id") or "") == offer.id:
                        parts.extend(_structured_context(link))
                        break
        enriched.append(_append_context(offer, parts))
    publication.structured_offers = enriched
    return publication

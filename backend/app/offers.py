from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import quote

import httpx
from pydantic import BaseModel


GOMA_BASE_URL = "https://goma.gg"
PRICE_RE = re.compile(r"(?P<value>\d{1,5}(?:[.,]\d{1,2})?)\s*kr\b", re.IGNORECASE)
QUANTITY_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|l|liter|ml|cl|stk\.?|pk\.?|pakke(?:r)?)\b",
    re.IGNORECASE,
)
DISCOUNT_RE = re.compile(r"^-?\d{1,3}\s*%$")


class Offer(BaseModel):
    id: str
    retailer: str
    product_name: str
    price: float
    normal_price: float | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount_percent: int | None = None
    image_url: str | None = None
    product_url: str | None = None
    source: str = "goma"


class OfferSearchResult(BaseModel):
    ok: bool = True
    query: str
    retailer: str
    source_url: str
    offers: list[Offer]
    parser: str


@dataclass
class _Script:
    attrs: dict[str, str]
    text: str


class _GomaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.scripts: list[_Script] = []
        self._script_attrs: dict[str, str] | None = None
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self._script_attrs = {key: value or "" for key, value in attrs}
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_attrs is not None:
            self.scripts.append(_Script(self._script_attrs, "".join(self._script_parts)))
            self._script_attrs = None
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_attrs is not None:
            self._script_parts.append(data)
            return
        value = " ".join(data.split())
        if value:
            self.text_parts.append(value)


def slugify_query(value: str) -> str:
    value = value.strip().casefold()
    value = value.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def goma_offer_url(query: str) -> str:
    slug = slugify_query(query)
    if not slug:
        raise ValueError("Query cannot be empty")
    return f"{GOMA_BASE_URL}/dagligvarer/{quote(slug)}/tilbud"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _first(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


def _retailer_name(mapping: dict[str, Any]) -> str | None:
    value = _first(mapping, ("retailer", "store", "shop", "merchant", "chain", "supermarket"))
    if isinstance(value, dict):
        value = _first(value, ("name", "title", "label"))
    return value.strip() if isinstance(value, str) and value.strip() else None


def _offer_from_mapping(mapping: dict[str, Any], retailer_filter: str) -> Offer | None:
    retailer = _retailer_name(mapping)
    if not retailer or retailer.casefold() != retailer_filter.casefold():
        return None

    name = _first(mapping, ("productName", "product_name", "name", "title", "label"))
    if isinstance(name, dict):
        name = _first(name, ("name", "title"))
    if not isinstance(name, str) or len(name.strip()) < 2:
        return None

    price = _number(_first(mapping, ("offerPrice", "salePrice", "currentPrice", "price", "amount")))
    if price is None:
        offers = mapping.get("offers")
        if isinstance(offers, dict):
            price = _number(_first(offers, ("price", "lowPrice")))
    if price is None:
        return None

    normal_price = _number(_first(mapping, ("normalPrice", "originalPrice", "beforePrice", "regularPrice", "listPrice")))
    quantity = _number(_first(mapping, ("quantity", "size", "content", "amountValue")))
    unit = _first(mapping, ("unit", "quantityUnit", "amountUnit"))
    if not isinstance(unit, str):
        unit = None

    unit_price = _first(mapping, ("unitPrice", "unit_price", "pricePerUnit"))
    if not isinstance(unit_price, str):
        unit_price = None

    discount_raw = _first(mapping, ("discountPercent", "discount_percentage", "discount"))
    discount = int(_number(discount_raw) or 0) or None

    image_url = _first(mapping, ("imageUrl", "image_url", "image"))
    if isinstance(image_url, dict):
        image_url = _first(image_url, ("url", "src"))
    if not isinstance(image_url, str):
        image_url = None

    product_url = _first(mapping, ("url", "productUrl", "product_url", "href"))
    if not isinstance(product_url, str):
        product_url = None

    stable = f"{retailer}|{name.strip()}|{price}|{quantity}|{unit}"
    return Offer(
        id=hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20],
        retailer=retailer,
        product_name=name.strip(),
        price=price,
        normal_price=normal_price,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        discount_percent=discount,
        image_url=image_url,
        product_url=product_url,
    )


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _parse_structured_scripts(scripts: list[_Script], retailer: str) -> list[Offer]:
    offers: dict[str, Offer] = {}
    for script in scripts:
        script_type = script.attrs.get("type", "").casefold()
        script_id = script.attrs.get("id", "").casefold()
        if "json" not in script_type and script_id != "__next_data__":
            continue
        try:
            payload = json.loads(script.text)
        except (json.JSONDecodeError, TypeError):
            continue
        for mapping in _walk_json(payload):
            offer = _offer_from_mapping(mapping, retailer)
            if offer:
                offers[offer.id] = offer
    return list(offers.values())


def _price_token(value: str) -> float | None:
    match = PRICE_RE.search(value)
    return _number(match.group("value")) if match else None


def _parse_text_fallback(parts: list[str], retailer: str) -> list[Offer]:
    """Best-effort parser for Goma's server-rendered offer cards.

    This intentionally only emits rows when a retailer token, a price and a
    plausible product name occur in a small local window. It prefers missing
    an offer to returning an incorrect one.
    """
    results: dict[str, Offer] = {}
    ignored = {
        "tilføj",
        "aktuel pris",
        "normalpris",
        "pris",
        "tilbud",
        "opdateres løbende",
    }

    for index, token in enumerate(parts):
        if token.casefold() != retailer.casefold():
            continue
        window = parts[index : index + 12]
        prices = [(i, _price_token(value)) for i, value in enumerate(window)]
        prices = [(i, price) for i, price in prices if price is not None]
        if not prices:
            continue

        price_index, price = prices[0]
        normal_price = prices[1][1] if len(prices) > 1 else None

        candidates: list[tuple[int, str]] = []
        for offset, value in enumerate(window[1:], start=1):
            compact = value.strip()
            lowered = compact.casefold()
            if (
                lowered in ignored
                or compact.casefold() == retailer.casefold()
                or PRICE_RE.fullmatch(compact)
                or DISCOUNT_RE.fullmatch(compact)
                or QUANTITY_RE.fullmatch(compact)
                or len(compact) < 3
            ):
                continue
            if "kr/" in lowered or lowered.endswith(" kr"):
                continue
            candidates.append((offset, compact))

        # Product labels are normally between retailer and pricing or directly
        # after the pricing fields. Prefer the closest descriptive token.
        candidates.sort(key=lambda entry: (abs(entry[0] - price_index), -len(entry[1])))
        if not candidates:
            continue
        _, product_name = candidates[0]

        quantity = None
        unit = None
        unit_price = None
        for value in window:
            quantity_match = QUANTITY_RE.search(value)
            if quantity_match and quantity is None:
                quantity = _number(quantity_match.group("value"))
                unit = quantity_match.group("unit").rstrip(".").casefold()
            if "kr/" in value.casefold() and unit_price is None:
                unit_price = value

        discount = None
        if normal_price and normal_price > price:
            discount = round((1 - price / normal_price) * 100)

        stable = f"{retailer}|{product_name}|{price}|{quantity}|{unit}"
        offer = Offer(
            id=hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20],
            retailer=retailer,
            product_name=product_name,
            price=price,
            normal_price=normal_price,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            discount_percent=discount,
        )
        results[offer.id] = offer

    return list(results.values())


def parse_goma_html(html: str, retailer: str) -> tuple[list[Offer], str]:
    parser = _GomaHTMLParser()
    parser.feed(html)

    structured = _parse_structured_scripts(parser.scripts, retailer)
    if structured:
        return sorted(structured, key=lambda offer: offer.price), "structured-json"

    fallback = _parse_text_fallback(parser.text_parts, retailer)
    return sorted(fallback, key=lambda offer: offer.price), "html-text"


async def fetch_goma_offers(
    query: str,
    retailer: str = "MENY",
    *,
    client: httpx.AsyncClient | None = None,
) -> OfferSearchResult:
    source_url = goma_offer_url(query)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 BaggerShopping/0.4 PoC",
                "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
            },
        )
    try:
        response = await client.get(source_url)
        response.raise_for_status()
        offers, parser_name = parse_goma_html(response.text, retailer)
        return OfferSearchResult(
            query=query,
            retailer=retailer,
            source_url=str(response.url),
            offers=offers,
            parser=parser_name,
        )
    finally:
        if owns_client:
            await client.aclose()

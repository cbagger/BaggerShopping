from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import date, datetime
from html.parser import HTMLParser

import httpx
from pydantic import BaseModel, Field


MENY_FLYER_URL = "https://ugensavis.meny.dk/"
WEEK_RE = re.compile(r"MENY\s+uge\s+(?P<week>\d{2})(?P<year>\d{2})", re.IGNORECASE)
VALIDITY_RE = re.compile(
    r"Avisen\s+g[æa]lder\s+fra\s+(?:[a-zæøå]+\s+)?(?P<from>\d{2}\.\d{2}\.\d{4})"
    r"\s+til\s+og\s+med\s+(?:[a-zæøå]+\s+)?(?P<until>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"(?<!\d)(?P<whole>\d{1,4})(?:[,.]\s?(?P<decimal>\d{2})|\s+(?P<space_decimal>\d{2})|[,.]-)(?!\d)")
QUANTITY_RE = re.compile(r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<unit>kg|g|l|liter|ml|cl|stk\.?|pk\.?)\b", re.IGNORECASE)


class Publication(BaseModel):
    id: str
    retailer: str = "MENY"
    title: str
    valid_from: str | None = None
    valid_until: str | None = None
    status: str = "current"
    source_url: str
    page_count: int = 0
    reader_url: str | None = None
    reader_kind: str | None = None
    week: int | None = None
    year: int | None = None
    content_source: str = "visible-html"
    text: str = ""
    page_texts: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    id: str
    retailer: str
    publication_id: str
    publication_title: str
    valid_from: str | None = None
    valid_until: str | None = None
    product_name: str
    brand: str | None = None
    price: float | None = None
    normal_price: float | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount_percent: int | None = None
    image_url: str | None = None
    source_url: str
    page_number: int | None = None
    raw_text: str
    safe_to_add: bool = False


class OfferSearchResult(BaseModel):
    ok: bool = True
    query: str
    retailer: str = "MENY"
    publication: Publication
    offers: list[Offer]

    @property
    def matches(self) -> list[str]:
        """Compatibility for the first read-only proof of concept."""
        return [offer.raw_text for offer in self.offers]


class _FlyerHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.script_parts: list[str] = []
        self._in_script = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered == "script":
            self._in_script = True
        elif lowered in {"style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "script":
            self._in_script = False
        elif lowered in {"style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_script:
            if data.strip():
                self.script_parts.append(data)
        elif not self._skip_depth:
            compact = " ".join(data.split())
            if compact:
                self.visible_parts.append(compact)


def _normalize_space(value: str) -> str:
    return " ".join(value.replace("\u00ad", "").replace("\u200b", "").replace("\\u0027", "'").replace("\\u0026", "&").split())


def _json_array_from_marker(source: str, marker: str) -> list[str]:
    start = source.find(marker)
    if start < 0 or (start := source.find("[", start + len(marker))) < 0:
        return []
    depth = slash_count = 0
    in_string = False
    end = None
    for index in range(start, len(source)):
        char = source[index]
        if char == "\\":
            slash_count += 1
            continue
        escaped = slash_count % 2 == 1
        slash_count = 0
        if char == '"' and not escaped:
            in_string = not in_string
        if not in_string:
            depth += char == "["
            depth -= char == "]"
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return []
    payload = source[start:end]
    for candidate in (payload, payload.replace('\\"', '"')):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [_normalize_space(str(item)) for item in parsed if str(item).strip()]
    return []


def _extract_page_texts(raw_html: str, scripts: list[str]) -> list[str]:
    for source in (raw_html, html_lib.unescape(raw_html), *scripts):
        for marker in ('"pageTexts":', '\\"pageTexts\\":'):
            if pages := _json_array_from_marker(source, marker):
                return pages
    return []


def _status(valid_from: str | None, valid_until: str | None, today: date | None = None) -> str:
    try:
        start = datetime.strptime(valid_from or "", "%d.%m.%Y").date()
        end = datetime.strptime(valid_until or "", "%d.%m.%Y").date()
    except ValueError:
        return "current"
    today = today or date.today()
    return "upcoming" if today < start else "expired" if today > end else "current"


def parse_meny_flyer_html(html: str, source_url: str = MENY_FLYER_URL) -> Publication:
    parser = _FlyerHTMLParser()
    parser.feed(html)
    visible_text = _normalize_space(" ".join(parser.visible_parts))
    metadata_text = _normalize_space(f"{visible_text} {' '.join(parser.script_parts)}")
    title_match = WEEK_RE.search(metadata_text)
    title = title_match.group(0) if title_match else "MENY ugens avis"
    validity = VALIDITY_RE.search(metadata_text)
    valid_from = validity.group("from") if validity else None
    valid_until = validity.group("until") if validity else None
    page_texts = _extract_page_texts(html, parser.script_parts)
    identity = hashlib.sha256(f"MENY|{title}|{valid_from}|{valid_until}|{source_url}".encode()).hexdigest()[:20]
    return Publication(
        id=identity,
        title=title,
        week=int(title_match.group("week")) if title_match else None,
        year=2000 + int(title_match.group("year")) if title_match else None,
        valid_from=valid_from,
        valid_until=valid_until,
        status=_status(valid_from, valid_until),
        source_url=source_url,
        reader_url=source_url,
        reader_kind="embedded-viewer",
        text=_normalize_space(" ".join(page_texts)) if page_texts else visible_text,
        page_texts=page_texts,
        page_count=len(page_texts),
        content_source="ipaper-pageTexts" if page_texts else "visible-html",
    )


def _price(match: re.Match[str]) -> float:
    decimal = match.group("decimal") or match.group("space_decimal") or "00"
    return float(f"{match.group('whole')}.{decimal}")


def _offer_price(text: str, start: int, end: int) -> float | None:
    for match in PRICE_RE.finditer(text, start, end):
        prefix = text[max(start, match.start() - 18):match.start()].casefold()
        if "pris" in prefix:
            continue
        return _price(match)
    return None


def _product_name(text: str, hit_start: int, hit_end: int) -> str:
    left = max(text.rfind(".", 0, hit_start), text.rfind("!", 0, hit_start), text.rfind("?", 0, hit_start)) + 1
    right_candidates = [pos for token in (".", "!", "?") if (pos := text.find(token, hit_end)) >= 0]
    right = min(right_candidates) if right_candidates else min(len(text), hit_end + 80)
    candidate = _normalize_space(text[left:right]).strip(" -–,:")
    if len(candidate) > 90:
        words = candidate.split()
        hit_word = next((i for i, word in enumerate(words) if text[hit_start:hit_end].casefold() in word.casefold()), len(words) // 2)
        candidate = " ".join(words[max(0, hit_word - 5):hit_word + 6])
    return candidate[:90]


def search_publication(publication: Publication, query: str) -> OfferSearchResult:
    query = query.strip()
    if not query:
        raise ValueError("Query cannot be empty")
    pages = publication.page_texts or [publication.text]
    offers: list[Offer] = []
    seen: set[str] = set()
    for page_number, page in enumerate(pages, 1):
        for hit in re.finditer(re.escape(query), page, re.IGNORECASE):
            raw = _normalize_space(page[max(0, hit.start() - 100):min(len(page), hit.end() + 180)])
            price = _offer_price(page, hit.end(), min(len(page), hit.end() + 180))
            quantity_match = QUANTITY_RE.search(raw)
            name = _product_name(page, hit.start(), hit.end())
            stable = hashlib.sha256(f"{publication.id}|{page_number}|{name}|{price}".encode()).hexdigest()[:20]
            if stable in seen:
                continue
            seen.add(stable)
            offers.append(Offer(
                id=stable,
                retailer="MENY",
                publication_id=publication.id,
                publication_title=publication.title,
                valid_from=publication.valid_from,
                valid_until=publication.valid_until,
                product_name=name or query,
                price=price,
                quantity=float(quantity_match.group("amount").replace(",", ".")) if quantity_match else None,
                unit=quantity_match.group("unit").rstrip(".") if quantity_match else None,
                source_url=publication.source_url,
                page_number=page_number if publication.page_texts else None,
                raw_text=raw,
                safe_to_add=bool(name and len(name) <= 80 and price is not None),
            ))
    return OfferSearchResult(query=query, publication=publication, offers=offers)


async def fetch_meny_flyer(*, client: httpx.AsyncClient | None = None) -> Publication:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 BaggerShopping/0.8", "Accept-Language": "da-DK,da;q=0.9"})
    try:
        response = await client.get(MENY_FLYER_URL)
        response.raise_for_status()
        return parse_meny_flyer_html(response.text, str(response.url))
    finally:
        if owns_client:
            await client.aclose()


async def search_live_meny_flyer(query: str, *, client: httpx.AsyncClient | None = None) -> OfferSearchResult:
    return search_publication(await fetch_meny_flyer(client=client), query)

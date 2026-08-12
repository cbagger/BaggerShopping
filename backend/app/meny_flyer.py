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
    page_image_urls: list[str] = Field(default_factory=list)
    reader_url: str | None = None
    reader_kind: str | None = None
    week: int | None = None
    year: int | None = None
    content_source: str = "visible-html"
    text: str = ""
    page_texts: list[str] = Field(default_factory=list)
    enrichment_urls: list[str] = Field(default_factory=list, exclude=True)
    structured_offers: list["Offer"] = Field(default_factory=list, exclude=True)


class OfferVariant(BaseModel):
    id: str
    name: str
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    matches_query: bool = False


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
    hotspot_x: float | None = None
    hotspot_y: float | None = None
    hotspot_width: float | None = None
    hotspot_height: float | None = None
    raw_text: str
    safe_to_add: bool = False
    variants: list[OfferVariant] = Field(default_factory=list)


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


def _json_object_from_marker(source: str, marker: str) -> dict:
    marker_start = source.find(marker)
    if marker_start < 0 or (start := source.find("{", marker_start + len(marker))) < 0:
        return {}
    depth = slash_count = 0
    in_string = False
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
            depth += char == "{"
            depth -= char == "}"
            if depth == 0:
                try:
                    value = json.loads(source[start:index + 1])
                except json.JSONDecodeError:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


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
    settings = _json_object_from_marker(html, "window.staticSettings =")
    pages = settings.get("pages") if isinstance(settings.get("pages"), list) else []
    aws = settings.get("aws") if isinstance(settings.get("aws"), dict) else {}
    page_base = str(aws.get("url") or "").rstrip("/")
    page_policy = str(aws.get("policy") or "")
    page_image_urls = [
        f"{page_base}/Pages/{page}/Normal.jpg" + (f"?{page_policy}" if page_policy else "")
        for page in pages
        if page_base and isinstance(page, int)
    ]
    chunk_urls = settings.get("enrichments", {}).get("chunkUrls", {})
    enrichment_urls = list(chunk_urls.values()) if isinstance(chunk_urls, dict) else []
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
        page_count=len(pages) or len(page_texts),
        page_image_urls=page_image_urls,
        content_source="ipaper-pageTexts" if page_texts else "visible-html",
        enrichment_urls=enrichment_urls,
    )


def _quantity(description: str | None) -> tuple[float | None, str | None]:
    match = QUANTITY_RE.search(description or "")
    if not match:
        return None, None
    amount = float(match.group("amount").replace(",", "."))
    unit = match.group("unit").rstrip(".").lower()
    # iPaper descriptions also contain nutrition, product IDs and campaign copy.
    # Only expose credible retail pack sizes; omitting a value is safer than a
    # confidently wrong quantity such as the observed "17 kg" watermelon.
    limits = {"kg": 10, "l": 10, "liter": 10, "g": 10_000, "ml": 10_000, "cl": 1_000, "stk": 100, "pk": 100}
    if amount <= 0 or amount > limits.get(unit, 10_000):
        return None, None
    # A description ending in a per-unit sales statement is stronger evidence
    # than a stray weight elsewhere in the supplier text. Do not attach a kg/l
    # amount to products explicitly sold per piece.
    if unit in {"kg", "g", "l", "liter", "ml", "cl"} and re.search(r"\bpr\.\s*stk\b", description or "", re.IGNORECASE):
        return None, None
    return amount, unit


def _friendly_product_name(name: str) -> str:
    replacements = (
        (re.compile(r"\bhakket\s+kødkvæg\b", re.IGNORECASE), "Hakket oksekød"),
        (re.compile(r"\bkylling\s+underlår\b", re.IGNORECASE), "Kyllingeunderlår"),
    )
    for pattern, replacement in replacements:
        name = pattern.sub(replacement, name)
    return _normalize_space(name)


def _finite_number(value: object) -> float | None:
    """Return an iPaper numeric value, including numbers encoded as strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", "."))
        except ValueError:
            return None
    else:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _hotspot_geometry(item: dict) -> tuple[float, float, float, float] | None:
    """Read geometry from both known iPaper enrichment representations."""
    candidates: list[object] = [item]
    for key in ("bounds", "rect", "rectangle", "position"):
        if isinstance(item.get(key), dict):
            candidates.append(item[key])

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        x = _finite_number(candidate.get("x", candidate.get("left")))
        y = _finite_number(candidate.get("y", candidate.get("top")))
        width = _finite_number(candidate.get("width", candidate.get("w")))
        height = _finite_number(candidate.get("height", candidate.get("h")))
        if None in (x, y, width, height) or width <= 0 or height <= 0:
            continue
        if max(x, y, width, height) > 1:
            x, y, width, height = x / 100, y / 100, width / 100, height / 100
        if 0 <= x < 1 and 0 <= y < 1 and x + width <= 1.01 and y + height <= 1.01:
            return x, y, width, height
    return None


PET_PRODUCT_RE = re.compile(
    r"\b(whiskas|frolic|pedigree|kattemad|hundefoder|hunde(?:mad)?|katte(?:mad)?|petfood)\b",
    re.IGNORECASE,
)

# Broad grocery domains are used only when the query clearly expresses one.
# They resolve semantic collisions ("salte fisk" sweets vs. fish, or
# "mælkechokolade" vs. milk) without teaching the search engine individual
# campaign products. Unknown queries continue through ordinary name matching.
DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pet", PET_PRODUCT_RE),
    ("confectionery", re.compile(r"(slik|chokolade|vingummi|lakrids|karamel|müslibar|muslibar|katjes|ritter\s+sport|corny)", re.IGNORECASE)),
    ("pantry", re.compile(r"(sauce|sovs|dressing|marinade)", re.IGNORECASE)),
    ("seafood", re.compile(r"\b(fisk(?:e(?:filet|fars)?)?|laks|torsk|sej|rødspætte|rejer?|skaldyr|tun)\b", re.IGNORECASE)),
    ("meat", re.compile(r"\b(oksekød|kødkvæg|hakket\s+kød|kylling|svinekød|gris|kalv|lam|culotte|bøf|kotelet)\b", re.IGNORECASE)),
    ("dairy", re.compile(r"(?:^|\b|[a-zæøå])(?:mælk|fløde|yoghurt|skyr|smør)(?:\b|$)", re.IGNORECASE)),
    ("beverage", re.compile(r"\b(juice|smoothie|saft|sodavand|cola|coca-cola|fanta|sprite|pepsi|schweppes|squash|ramlösa|vand|øl|vin)\b", re.IGNORECASE)),
    ("bakery", re.compile(r"\b(rugbrød|hvedebrød|franskbrød|toastbrød|boller|brød)\b", re.IGNORECASE)),
    ("produce", re.compile(r"\b(frugt|grønt|grøntsag|melon|æble|pære|banan|tomat|kartoffel|gulerod)\b", re.IGNORECASE)),
)

QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "sodavand": ("cola", "coca-cola", "fanta", "sprite", "pepsi", "schweppes", "squash"),
}

# Most Danish grocery words legitimately occur in compounds (skummetmælk,
# sandwichrugbrød). A few short terms produce unrelated substring hits and
# therefore require token boundaries.
STRICT_TOKEN_TERMS = {"cola"}


def _product_domain(value: str) -> str | None:
    normalized = _normalize_space(value)
    return next((domain for domain, pattern in DOMAIN_PATTERNS if pattern.search(normalized)), None)


def _is_pet_offer(offer: Offer) -> bool:
    searchable = " ".join([offer.product_name, *(variant.name for variant in offer.variants)])
    return bool(PET_PRODUCT_RE.search(searchable))


def _query_terms(query: str) -> tuple[str, ...]:
    needle = _normalize_space(query).casefold()
    return (needle, *QUERY_ALIASES.get(needle, ()))


def _contains_query_term(value: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize_space(value).casefold()
    for index, term in enumerate(terms):
        if index > 0 or term in STRICT_TOKEN_TERMS:
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized):
                return True
        elif term in normalized:
            return True
    return False


def parse_enrichment_chunks(publication: Publication, chunks: list[dict]) -> list[Offer]:
    all_items: list[dict] = []
    for chunk in chunks:
        enrichments = chunk.get("enrichments", []) if isinstance(chunk, dict) else []
        all_items.extend(item for item in enrichments if isinstance(item, dict))

    # iPaper represents a campaign as one positioned type-6 marker and many
    # unpositioned type-13 shop variants.  A variant normally points at the
    # marker through parentid.  pageIndex + alttext is retained as a fallback
    # for publications where that reference is omitted.
    markers_by_id: dict[str, dict] = {}
    markers_by_label: dict[tuple[int, str], list[dict]] = {}
    for item in all_items:
        if item.get("type") != 6 or _hotspot_geometry(item) is None:
            continue
        marker_id = str(item.get("id") or "")
        if marker_id:
            markers_by_id[marker_id] = item
        page_index = int(_finite_number(item.get("pageIndex")) or 0)
        label = _normalize_space(str(item.get("alttext") or "")).casefold()
        if label:
            markers_by_label.setdefault((page_index, label), []).append(item)

    groups: dict[tuple[int, str, float], list[dict]] = {}
    for item in all_items:
        if item.get("type") != 13:
            continue
        name = _normalize_space(str(item.get("name") or ""))
        label = _normalize_space(str(item.get("alttext") or name))
        price = _finite_number(item.get("price"))
        if not name or not label or price is None:
            continue
        page_index = int(_finite_number(item.get("pageIndex")) or 0)
        key = (page_index + 1, label.casefold(), price)
        groups.setdefault(key, []).append(item)

    offers: list[Offer] = []
    for (page_number, _, price), items in groups.items():
        label = _normalize_space(str(items[0].get("alttext") or items[0]["name"]))
        variants: list[OfferVariant] = []
        seen_products: set[str] = set()
        for item in items:
            product_id = str(item.get("productId") or item.get("id") or "")
            name = _normalize_space(str(item["name"]))
            # iPaper appends the shared advert heading in parentheses to variant names.
            suffix = f" ({label})"
            if name.casefold().endswith(suffix.casefold()):
                name = name[:-len(suffix)].strip()
            name = _friendly_product_name(name)
            identity = product_id or hashlib.sha256(f"{page_number}|{name}".encode()).hexdigest()[:20]
            if identity in seen_products:
                continue
            seen_products.add(identity)
            description = _normalize_space(str(item.get("desc") or "")) or None
            quantity, unit = _quantity(description)
            variants.append(OfferVariant(id=identity, name=name, description=description, quantity=quantity, unit=unit))
        if not variants:
            continue
        stable = hashlib.sha256(f"{publication.id}|{page_number}|{label}|{price}".encode()).hexdigest()[:20]
        quantity, unit = (variants[0].quantity, variants[0].unit) if len(variants) == 1 else (None, None)
        marker_items: list[dict] = []
        seen_marker_ids: set[str] = set()
        for item in items:
            parent_id = str(item.get("parentid") or "")
            marker = markers_by_id.get(parent_id)
            if marker is not None and str(marker.get("id")) not in seen_marker_ids:
                marker_items.append(marker)
                seen_marker_ids.add(str(marker.get("id")))
        if not marker_items:
            page_index = page_number - 1
            marker_items = markers_by_label.get((page_index, label.casefold()), [])

        geometries = [
            geometry
            for item in (*items, *marker_items)
            if (geometry := _hotspot_geometry(item)) is not None
        ]
        xs = [geometry[0] for geometry in geometries]
        ys = [geometry[1] for geometry in geometries]
        rights = [geometry[0] + geometry[2] for geometry in geometries]
        bottoms = [geometry[1] + geometry[3] for geometry in geometries]
        hotspot_x = min(xs) if xs else None
        hotspot_y = min(ys) if ys else None
        hotspot_width = max(rights) - hotspot_x if rights and hotspot_x is not None else None
        hotspot_height = max(bottoms) - hotspot_y if bottoms and hotspot_y is not None else None
        offers.append(Offer(
            id=stable,
            retailer=publication.retailer,
            publication_id=publication.id,
            publication_title=publication.title,
            valid_from=publication.valid_from,
            valid_until=publication.valid_until,
            product_name=label,
            price=price,
            image_url=(
                publication.page_image_urls[page_number - 1]
                if 0 < page_number <= len(publication.page_image_urls)
                else None
            ),
            quantity=quantity,
            unit=unit,
            source_url=publication.source_url,
            page_number=page_number,
            hotspot_x=hotspot_x,
            hotspot_y=hotspot_y,
            hotspot_width=hotspot_width,
            hotspot_height=hotspot_height,
            raw_text=" | ".join(filter(None, (variant.description for variant in variants))),
            safe_to_add=True,
            variants=variants,
        ))
    return sorted(offers, key=lambda offer: (offer.page_number or 0, offer.product_name.casefold()))


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
    if publication.structured_offers:
        needle = _normalize_space(query).casefold()
        query_terms = _query_terms(query)
        query_domain = _product_domain(needle)
        matches: list[Offer] = []
        for source in publication.structured_offers:
            offer = source.model_copy(deep=True)
            # Ingredient searches such as "oksekød" must not surface pet food
            # merely because a Whiskas/Frolic variant contains that flavour.
            # Explicit pet-food searches remain supported.
            if _is_pet_offer(offer) and not PET_PRODUCT_RE.search(needle):
                continue
            label_matches = _contains_query_term(offer.product_name, query_terms)
            matching_variants = [
                variant for variant in offer.variants
                if _contains_query_term(variant.name, query_terms)
                and (query_domain is None or _product_domain(variant.name) in {None, query_domain})
            ]
            if label_matches and query_domain is not None:
                label_matches = _product_domain(offer.product_name) in {None, query_domain}
            # Descriptions and raw advert text are deliberately excluded. They
            # contain recipes, legal copy and hidden group data that produced
            # unrelated results such as pet food for an "oksekød" search.
            if not label_matches and not matching_variants:
                continue
            if not matching_variants and label_matches:
                matching_variants = [
                    variant for variant in offer.variants
                    if query_domain is None or _product_domain(variant.name) in {None, query_domain}
                ]
            for variant in matching_variants:
                variant.matches_query = True
            # A search response exposes only variants relevant to the query,
            # never the entire campaign family.
            offer.variants = sorted(matching_variants, key=lambda variant: variant.name.casefold())
            if offer.variants:
                matches.append(offer)
        return OfferSearchResult(query=query, publication=publication, offers=matches)

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
                retailer=publication.retailer,
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
        client = httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 BaggerShopping/0.10", "Accept-Language": "da-DK,da;q=0.9"})
    try:
        response = await client.get(MENY_FLYER_URL)
        response.raise_for_status()
        publication = parse_meny_flyer_html(response.text, str(response.url))
        chunks: list[dict] = []
        for url in publication.enrichment_urls:
            chunk_response = await client.get(url)
            chunk_response.raise_for_status()
            payload = chunk_response.json()
            if isinstance(payload, dict):
                chunks.append(payload)
        publication.structured_offers = parse_enrichment_chunks(publication, chunks)
        return publication
    finally:
        if owns_client:
            await client.aclose()


async def search_live_meny_flyer(query: str, *, client: httpx.AsyncClient | None = None) -> OfferSearchResult:
    return search_publication(await fetch_meny_flyer(client=client), query)

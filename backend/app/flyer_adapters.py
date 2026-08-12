from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from .meny_flyer import (
    Offer,
    OfferVariant,
    Publication,
    _normalize_space,
    _status,
    fetch_meny_flyer,
    parse_enrichment_chunks,
    parse_meny_flyer_html,
)


RETAILER_ORDER = ("MENY", "365discount", "REMA 1000", "Bilka", "føtex", "Lidl", "Netto", "SPAR")


@dataclass(frozen=True)
class RetailerSource:
    retailer: str
    landing_url: str
    viewer_hosts: tuple[str, ...]
    link_words: tuple[str, ...] = ("avis", "tilbudsavis", "uge")
    tjek_dealer_id: str | None = None


SOURCES: tuple[RetailerSource, ...] = (
    RetailerSource("365discount", "https://365discount.coop.dk/365avis/", ("365discount.coop.dk", "tjek.com", "ipaper.io"), tjek_dealer_id="DWZE1w"),
    RetailerSource("REMA 1000", "https://rema1000.dk/avis", ("avis.rema1000.dk", "ipaper.io", "view.publitas.com"), tjek_dealer_id="11deC"),
    RetailerSource("Bilka", "https://www.bilka.dk/bilkaavisen/", ("avis.bilka.dk",), tjek_dealer_id="93f13"),
    RetailerSource("føtex", "https://www.foetex.dk/foetex-avis/", ("avis.foetex.dk",), tjek_dealer_id="bdf5A"),
    RetailerSource("Lidl", "https://www.lidl.dk/c/tilbudsavis/s10013730", ("leaflets.schwarz", "lidl.dk"), tjek_dealer_id="71c90"),
    RetailerSource("Netto", "https://netto.dk/netto-avisen/", ("viewer.ipaper.io", "netto.dk", "tjek.com"), tjek_dealer_id="9ba51"),
    RetailerSource("SPAR", "https://spar.dk/ugensavis", ("ipaper.io", "view.publitas.com", "spar.dk"), tjek_dealer_id="88ddE"),
)


DATE_RANGE_RE = re.compile(
    r"(?:fra\s+)?(?:[a-zæøå]+\s+)?(?:den\s+|d\.\s*)?(?P<from_day>\d{1,2})(?:[./-](?P<from_month>\d{1,2})(?:[./-](?P<from_year>\d{2,4}))?|\.?\s+(?P<from_month_name>[a-zæøå]+))"
    r"\s*(?:til(?:\s+og\s+med)?|[-–])\s*"
    r"(?:[a-zæøå]+\s+)?(?:den\s+|d\.\s*)?(?P<until_day>\d{1,2})(?:[./-](?P<until_month>\d{1,2})(?:[./-](?P<until_year>\d{2,4}))?|\.?\s+(?P<until_month_name>[a-zæøå]+))",
    re.IGNORECASE,
)
MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}


@dataclass
class FlyerLink:
    url: str
    text: str
    external_id: str | None = None


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[FlyerLink] = []
        self._href: str | None = None
        self._external_id: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        attributes = dict(attrs)
        if lowered == "a":
            self._href = attributes.get("href")
            self._external_id = attributes.get("data-track-id") or attributes.get("data-publication-id")
            self._parts = []
        elif lowered == "iframe" and attributes.get("src"):
            # SPAR and similar sites embed the official reader instead of
            # exposing it as an ordinary anchor.
            self.links.append(FlyerLink(attributes["src"], attributes.get("title") or "tilbudsavis"))

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            self.links.append(FlyerLink(self._href, _normalize_space(" ".join(self._parts)), self._external_id))
            self._href = None
            self._external_id = None
            self._parts = []


PAGE_NUMBER_RE = re.compile(r"(?:p-|page[-_/])(?P<page>\d{1,3})(?:\D|$)", re.IGNORECASE)
ABSOLUTE_URL_RE = re.compile(r'https?://[^"\'<>\s\\]+')


def _decoded_html(raw_html: str) -> str:
    decoded = html_lib.unescape(raw_html).replace("\\u002F", "/").replace("\\/", "/")
    return re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        decoded,
    )


def _page_number(url: str) -> int | None:
    decoded = html_lib.unescape(url)
    match = PAGE_NUMBER_RE.search(decoded)
    if not match:
        # iPaper's canonical image path is /Pages/<number>/Normal.jpg.
        match = re.search(r"/Pages/(?P<page>\d{1,3})/", decoded, re.IGNORECASE)
    return int(match.group("page")) if match else None


def extract_embedded_page_images(raw_html: str) -> list[str]:
    """Extract ordered leaflet pages from Tjek and Schwarz landing markup.

    Those readers publish page images in serialized HTML rather than in
    iPaper's ``window.staticSettings`` object.  Keep the original signed URL;
    rebuilding Tjek/imgproxy signatures would invalidate them.
    """
    decoded = _decoded_html(raw_html)
    candidates = ABSOLUTE_URL_RE.findall(decoded)
    by_page: dict[int, str] = {}
    for candidate in candidates:
        candidate = candidate.rstrip("),;]")
        lowered = candidate.casefold()
        if not (
            ("image-transformer-api.tjek.com" in lowered or "imgproxy.leaflets.schwarz" in lowered)
            and re.search(r"(?:p-|page[-_/])\d{1,3}", lowered)
        ):
            continue
        page = _page_number(candidate)
        if page is None:
            continue
        # Prefer the largest explicitly requested rendition when duplicate
        # responsive image URLs for the same page are present.
        current = by_page.get(page)
        width = int((re.search(r"(?:[?&]w=|rs:fit:)(\d+)", candidate) or [None, "0"])[1])
        current_width = int((re.search(r"(?:[?&]w=|rs:fit:)(\d+)", current or "") or [None, "0"])[1])
        if current is None or width >= current_width:
            by_page[page] = candidate
    return [by_page[page] for page in sorted(by_page)]


def extract_ipaper_minipaper(raw_html: str) -> tuple[list[str], str]:
    """Read Dagrofa's compact iPaper configuration used by SPAR."""
    decoded = _decoded_html(raw_html)
    count_match = re.search(r'"numberOfPages"\s*:\s*(\d+)', decoded)
    aws_match = re.search(
        r'"aws"\s*:\s*\{\s*"policy"\s*:\s*"([^"]+)"\s*,\s*"url"\s*:\s*"([^"]+)"',
        decoded,
    )
    if not count_match or not aws_match:
        return [], ""
    count = int(count_match.group(1))
    policy = aws_match.group(1).replace("\\u0026", "&")
    base = aws_match.group(2).rstrip("/")
    return [f"{base}/Pages/{page}/Normal.jpg?{policy}" for page in range(1, count + 1)], decoded


def _iso_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("Europe/Copenhagen"))
    return parsed.strftime("%d.%m.%Y")


def _tjek_ids(raw_html: str) -> list[str]:
    decoded = _decoded_html(raw_html)
    values = re.findall(r'data-publication-id=["\']?([A-Za-z0-9_-]{6,80})', decoded, re.IGNORECASE)
    values += re.findall(r'/v\d+/catalogs/([A-Za-z0-9_-]{6,80})', decoded, re.IGNORECASE)
    return list(dict.fromkeys(values))


def _publication_from_tjek(payload: dict, pages: object, source: RetailerSource, reader_url: str) -> Publication:
    page_rows = pages if isinstance(pages, list) else []
    page_images = [row.get("view") for row in page_rows if isinstance(row, dict) and row.get("view")]
    valid_from = _iso_date(payload.get("run_from"))
    valid_until = _iso_date(payload.get("run_till"))
    title = _normalize_space(str(payload.get("label") or f"{source.retailer} tilbudsavis"))
    identity = hashlib.sha256(f"{source.retailer}|{payload.get('id')}".encode()).hexdigest()[:20]
    return Publication(
        id=identity,
        retailer=source.retailer,
        title=title,
        valid_from=valid_from,
        valid_until=valid_until,
        status=_status(valid_from, valid_until),
        source_url=reader_url,
        reader_url=reader_url,
        reader_kind="tjek-pages",
        page_count=len(page_images),
        page_image_urls=page_images,
        content_source="tjek-catalog-api",
    )


def _quantity(payload: object) -> tuple[float | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    size = payload.get("size") if isinstance(payload.get("size"), dict) else {}
    unit = payload.get("unit") if isinstance(payload.get("unit"), dict) else {}
    value = size.get("from")
    return (float(value), str(unit.get("symbol"))) if isinstance(value, (int, float)) and unit.get("symbol") else (None, None)


def _variant_strings(payload: dict) -> list[str]:
    """Collect product alternatives supplied by Tjek's changing offer schema.

    Depending on the retailer, alternatives have appeared as strings, product
    dictionaries, or nested lists below ``variants``/``products``/``items``.
    Do not treat marketing descriptions as product names.
    """
    values: list[str] = []

    def visit(value: object, *, variant_context: bool = False) -> None:
        if isinstance(value, str):
            if variant_context:
                cleaned = _normalize_space(value).strip(" -*")
                if 2 <= len(cleaned) <= 120:
                    values.append(cleaned)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, variant_context=variant_context)
            return
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            lowered = str(key).casefold()
            child_context = variant_context or lowered in {
                "variants", "variant", "products", "product_variants",
                "choices", "alternatives", "items",
            }
            if child_context and lowered in {"name", "title", "label", "heading"}:
                visit(child, variant_context=True)
            elif lowered in {"variants", "variant", "products", "product_variants", "choices", "alternatives", "items"}:
                visit(child, variant_context=True)

    visit(payload)
    return list(dict.fromkeys(values))


def _tjek_variants(identity: str, heading: str, description: str | None, quantity: float | None, unit: str | None, payload: dict) -> list[OfferVariant]:
    """Expose every explicit alternative from Tjek before heading fallback."""
    names = _variant_strings(payload)
    normalized = heading.replace(" / ", ", ")
    if not names:
        names = [_normalize_space(value) for value in re.split(r"\s*,\s*|\s+eller\s+", normalized, flags=re.IGNORECASE)]
    names = [name for name in names if len(name) >= 2]
    if not 1 < len(names) <= 8:
        names = [heading]
    return [
        OfferVariant(id=f"{identity}-{index}", name=name, description=description, quantity=quantity, unit=unit)
        for index, name in enumerate(dict.fromkeys(names))
    ]


def _offers_by_id(rows: object) -> dict[str, dict]:
    if not isinstance(rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }


def parse_tjek_hotspots(
    publication: Publication,
    rows: object,
    offer_rows: object = None,
) -> list[Offer]:
    """Convert Tjek's official offer polygons into the common MENY model."""
    offers: list[Offer] = []
    if not isinstance(rows, list):
        return offers
    detailed_offers = _offers_by_id(offer_rows)
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "offer":
            continue
        hotspot_payload = row.get("offer") if isinstance(row.get("offer"), dict) else row
        offer_id = str(hotspot_payload.get("id") or row.get("id") or "")
        # /catalogs/{id}/hotspots is authoritative for geometry, while the
        # public /v2/offers feed contains the complete description, original
        # price, crop image and validity. Both use the same offer id.
        payload = {**hotspot_payload, **detailed_offers.get(offer_id, {})}
        heading = _normalize_space(str(payload.get("heading") or row.get("heading") or "")).rstrip("*")
        locations = row.get("locations") if isinstance(row.get("locations"), dict) else {}
        pricing = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else {}
        quantity, unit = _quantity(payload.get("quantity"))
        for page_key, polygon in locations.items():
            try:
                page = int(page_key)
                points = [(float(point[0]), float(point[1])) for point in polygon if len(point) >= 2]
            except (TypeError, ValueError):
                continue
            if not heading or not points:
                continue
            xs, ys = zip(*points)
            # Tjek coordinates use page width as the unit on both axes. Their
            # portrait pages are sqrt(2) units high; SwiftUI uses 0...1.
            page_aspect = 2 ** 0.5
            x, y = max(0.0, min(xs)), max(0.0, min(ys) / page_aspect)
            width = min(1.0 - x, (max(xs) - min(xs)))
            height = min(1.0 - y, (max(ys) - min(ys)) / page_aspect)
            identity = str(payload.get("id") or row.get("id") or hashlib.sha256(f"{publication.id}|{page}|{heading}".encode()).hexdigest()[:20])
            variants = _tjek_variants(identity, heading, payload.get("description"), quantity, unit, payload)
            images = payload.get("images") if isinstance(payload.get("images"), dict) else {}
            offers.append(Offer(
                id=f"{identity}-{page}", retailer=publication.retailer,
                publication_id=publication.id, publication_title=publication.title,
                valid_from=publication.valid_from, valid_until=publication.valid_until,
                product_name=heading, price=pricing.get("price"), normal_price=pricing.get("pre_price"),
                quantity=quantity, unit=unit,
                # Prefer Tjek's official offer crop. Besides being sharper in
                # search results, it is the smallest possible input for the
                # client-side variant recognizer and avoids server-side image
                # downloads/cache growth.
                image_url=images.get("zoom") or images.get("view") or (
                    publication.page_image_urls[page - 1]
                    if 0 < page <= len(publication.page_image_urls) else None
                ),
                source_url=publication.source_url, page_number=page,
                hotspot_x=x, hotspot_y=y, hotspot_width=width, hotspot_height=height,
                raw_text=_normalize_space(" ".join(filter(None, (heading, payload.get("description"))))),
                safe_to_add=True, variants=variants,
            ))
    return offers


def _publication_from_schwarz(payload: dict, source: RetailerSource, reader_url: str) -> Publication:
    flyer = payload.get("flyer") if isinstance(payload.get("flyer"), dict) else {}
    rows = flyer.get("pages") if isinstance(flyer.get("pages"), list) else []
    page_images = [
        row.get("image") or row.get("zoom") or row.get("thumbnail")
        for row in rows if isinstance(row, dict) and (row.get("image") or row.get("zoom") or row.get("thumbnail"))
    ]
    valid_from = _iso_date(flyer.get("offerStartDate") or flyer.get("startDate"))
    valid_until = _iso_date(flyer.get("offerEndDate") or flyer.get("endDate"))
    title = _normalize_space(" - ".join(
        str(value) for value in (flyer.get("name"), flyer.get("title")) if value
    ) or "Lidl tilbudsavis")
    identity = hashlib.sha256(f"Lidl|{flyer.get('id')}".encode()).hexdigest()[:20]
    publication = Publication(
        id=identity, retailer=source.retailer, title=title,
        valid_from=valid_from, valid_until=valid_until, status=_status(valid_from, valid_until),
        source_url=reader_url, reader_url=reader_url, reader_kind="schwarz-pages",
        page_count=len(page_images), page_image_urls=page_images, content_source="schwarz-flyer-api",
    )
    products = flyer.get("products") if isinstance(flyer.get("products"), dict) else {}
    for page_index, page in enumerate(rows, start=1):
        if not isinstance(page, dict):
            continue
        for link in page.get("links") or []:
            if not isinstance(link, dict) or link.get("displayType") != "product":
                continue
            product = products.get(str(link.get("id")), {})
            if not isinstance(product, dict):
                product = {}
            details = link.get("productDetails") if isinstance(link.get("productDetails"), dict) else {}
            name = _normalize_space(str(product.get("title") or details.get("title") or link.get("title") or ""))
            if not name:
                continue
            identity = str(link.get("id") or details.get("productId") or hashlib.sha256(f"{publication.id}|{page_index}|{name}".encode()).hexdigest()[:20])
            try:
                price = float(str(product.get("price")).replace(",", ".")) if product.get("price") is not None else None
                x, y, width, height = (float(link[key]) / 100 for key in ("left", "top", "width", "height"))
            except (TypeError, ValueError, KeyError):
                continue
            variant = OfferVariant(id=identity, name=name, description=product.get("description"))
            publication.structured_offers.append(Offer(
                id=identity, retailer=source.retailer, publication_id=publication.id,
                publication_title=publication.title, valid_from=publication.valid_from,
                valid_until=publication.valid_until, product_name=name, brand=product.get("brand"),
                price=price, image_url=product.get("image") or page_images[page_index - 1],
                source_url=str(product.get("url") or link.get("url") or reader_url), page_number=page_index,
                hotspot_x=x, hotspot_y=y, hotspot_width=width, hotspot_height=height,
                raw_text=_normalize_space(" ".join(filter(None, (name, product.get("description"))))),
                safe_to_add=True, variants=[variant],
            ))
    return publication


def _publication_from_html(raw_html: str, source: RetailerSource, link: FlyerLink) -> Publication:
    publication = _retarget(parse_meny_flyer_html(raw_html, link.url), source, link)
    if not publication.page_image_urls:
        minipaper_pages, minipaper_text = extract_ipaper_minipaper(raw_html)
        if minipaper_pages:
            publication.page_image_urls = minipaper_pages
            publication.page_count = len(minipaper_pages)
            publication.reader_kind = "ipaper-minipaper"
            publication.content_source = "ipaper-minipaper"
            valid_from, valid_until = validity_from_text(minipaper_text)
            publication.valid_from = valid_from or publication.valid_from
            publication.valid_until = valid_until or publication.valid_until
            publication.status = _status(publication.valid_from, publication.valid_until)
    if not publication.page_image_urls:
        publication.page_image_urls = extract_embedded_page_images(raw_html)
        publication.page_count = len(publication.page_image_urls)
        if publication.page_image_urls:
            publication.reader_kind = (
                "schwarz-pages" if "leaflets.schwarz" in publication.page_image_urls[0] else "tjek-pages"
            )
            publication.content_source = publication.reader_kind
    return publication


def _year(value: str | None, fallback: int) -> int:
    if not value:
        return fallback
    result = int(value)
    return 2000 + result if result < 100 else result


def validity_from_text(text: str, *, today: date | None = None) -> tuple[str | None, str | None]:
    match = DATE_RANGE_RE.search(text)
    if not match:
        return None, None
    today = today or date.today()
    from_month = int(match.group("from_month") or 0) or MONTHS.get((match.group("from_month_name") or "").casefold())
    until_month = int(match.group("until_month") or 0) or MONTHS.get((match.group("until_month_name") or "").casefold())
    if from_month is None and until_month is not None:
        from_month = until_month
    if not from_month or not until_month:
        return None, None
    until_year = _year(match.group("until_year"), today.year)
    from_year = _year(match.group("from_year"), until_year)
    if from_month > until_month and not match.group("from_year"):
        from_year = until_year - 1
    try:
        start = date(from_year, from_month, int(match.group("from_day")))
        end = date(until_year, until_month, int(match.group("until_day")))
    except ValueError:
        return None, None
    return start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")


def discover_flyer_links(html: str, source: RetailerSource) -> list[FlyerLink]:
    parser = _LinkParser()
    parser.feed(html)
    result: list[FlyerLink] = []
    seen: set[str] = set()
    for link in parser.links:
        url = urljoin(source.landing_url, link.url)
        hostname = (urlparse(url).hostname or "").casefold()
        text = _normalize_space(link.text)
        relevant_host = any(host.casefold() in hostname for host in source.viewer_hosts)
        relevant_text = any(word in text.casefold() for word in source.link_words)
        if url not in seen and relevant_host and relevant_text:
            seen.add(url)
            result.append(FlyerLink(url, text or f"{source.retailer} tilbudsavis", link.external_id))
    # Some Tjek and Schwarz integrations serialize their viewer URL instead of
    # rendering an anchor/iframe.  Discover only URLs on the allow-listed
    # official hosts for this retailer.
    for candidate in (() if result else ABSOLUTE_URL_RE.findall(_decoded_html(html))):
        candidate = candidate.rstrip("),;]")
        hostname = (urlparse(candidate).hostname or "").casefold()
        if candidate in seen or not any(host.casefold() in hostname for host in source.viewer_hosts):
            continue
        lowered = candidate.casefold()
        if any(marker in lowered for marker in ("catalog", "tilbudsavis", "leaflet", "/avis", "viewer")):
            seen.add(candidate)
            result.append(FlyerLink(candidate, f"{source.retailer} tilbudsavis"))
    return result


def _retarget(publication: Publication, source: RetailerSource, link: FlyerLink) -> Publication:
    valid_from, valid_until = validity_from_text(link.text)
    publication.retailer = source.retailer
    publication.title = link.text or publication.title.replace("MENY", source.retailer)
    publication.valid_from = valid_from or publication.valid_from
    publication.valid_until = valid_until or publication.valid_until
    publication.status = _status(publication.valid_from, publication.valid_until)
    publication.source_url = link.url
    publication.reader_url = link.url
    publication.id = hashlib.sha256(
        f"{source.retailer}|{publication.title}|{publication.valid_from}|{publication.valid_until}|{link.url}".encode()
    ).hexdigest()[:20]
    return publication


async def fetch_retailer_publications(
    source: RetailerSource, *, client: httpx.AsyncClient
) -> list[Publication]:
    landing: httpx.Response | None = None
    try:
        landing = await client.get(source.landing_url)
        landing.raise_for_status()
    except httpx.HTTPError:
        if not source.tjek_dealer_id:
            raise
    landing_html = landing.text if landing is not None else ""
    landing_url = str(landing.url) if landing is not None else source.landing_url
    links = discover_flyer_links(landing_html, source)
    if source.tjek_dealer_id:
        # The dealer feed is authoritative and already includes current,
        # upcoming and supplementary editions. Parsing the shelf page as one
        # giant flyer would merge pages from several catalogues.
        links = []
    # Several retailers embed every page directly on the landing page. Treat
    # that page as the official reader instead of requiring a synthetic link.
    if not source.tjek_dealer_id and extract_embedded_page_images(landing_html):
        links.insert(0, FlyerLink(landing_url, f"{source.retailer} tilbudsavis"))
    publications: list[Publication] = []
    seen_publications: set[tuple[str, ...]] = set()
    # Tjek/ShopGun exposes complete signed page lists via its catalogue API.
    # 365 embeds the ID directly; REMA's region-protected site may expose it in
    # links or scripts. IDs found here are always resolved against Tjek itself.
    tjek_ids = _tjek_ids(landing_html)
    if source.tjek_dealer_id:
        try:
            catalog_response = await client.get(
                "https://squid-api.tjek.com/v2/catalogs",
                params={"dealer_id": source.tjek_dealer_id},
            )
            catalog_response.raise_for_status()
            catalog_rows = catalog_response.json()
            if isinstance(catalog_rows, list):
                tjek_ids.extend(
                    str(row["id"]) for row in catalog_rows
                    if isinstance(row, dict) and row.get("id")
                )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            pass
    for catalog_id in dict.fromkeys(tjek_ids):
        try:
            metadata_response, pages_response, hotspots_response, offers_response = await asyncio.gather(
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}"),
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/pages?w=700"),
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/hotspots"),
                client.get(
                    "https://api.etilbudsavis.dk/v2/offers",
                    # The public offers endpoint filters by dealer, not by
                    # catalogue. Offer IDs are globally stable and are joined
                    # to this catalogue's hotspots below.
                    params={"dealer_id": source.tjek_dealer_id, "limit": 1000},
                ),
            )
            metadata_response.raise_for_status()
            pages_response.raise_for_status()
            publication = _publication_from_tjek(
                metadata_response.json(), pages_response.json(), source, landing_url
            )
            if hotspots_response.is_success:
                detailed_rows = offers_response.json() if offers_response.is_success else []
                publication.structured_offers = parse_tjek_hotspots(
                    publication, hotspots_response.json(), detailed_rows
                )
            fingerprint = tuple(publication.page_image_urls)
            if publication.page_count and fingerprint not in seen_publications:
                seen_publications.add(fingerprint)
                publications.append(publication)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            continue

    for link in links:
        try:
            if source.retailer == "Lidl" and link.external_id:
                api_response = await client.get(
                    "https://endpoints.leaflets.schwarz/v4/flyer",
                    params={"flyer_identifier": link.external_id},
                )
                api_response.raise_for_status()
                publication = _publication_from_schwarz(api_response.json(), source, link.url)
                fingerprint = tuple(publication.page_image_urls)
                if publication.page_count and fingerprint not in seen_publications:
                    seen_publications.add(fingerprint)
                    publications.append(publication)
                continue
            if landing is not None and link.url == str(landing.url):
                response = landing
            else:
                response = await client.get(link.url)
                response.raise_for_status()
            publication = _publication_from_html(response.text, source, link)
            # Tjek publication IDs are sometimes present only after following
            # the retailer's PDF/current-edition link (not on the shelf page).
            if not publication.page_count:
                for catalog_id in _tjek_ids(response.text):
                    metadata_response, pages_response = await asyncio.gather(
                        client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}"),
                        client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/pages?w=700"),
                    )
                    metadata_response.raise_for_status()
                    pages_response.raise_for_status()
                    candidate = _publication_from_tjek(
                        metadata_response.json(), pages_response.json(), source, link.url
                    )
                    if candidate.page_count:
                        publication = candidate
                        break
            chunks: list[dict] = []
            for url in publication.enrichment_urls:
                chunk_response = await client.get(url)
                chunk_response.raise_for_status()
                payload = chunk_response.json()
                if isinstance(payload, dict):
                    chunks.append(payload)
            publication.structured_offers = parse_enrichment_chunks(publication, chunks)
            fingerprint = tuple(publication.page_image_urls)
            usable_spar = not (
                source.retailer == "SPAR" and publication.page_count <= 1
                and not publication.valid_until
            )
            if publication.page_count and usable_spar and fingerprint not in seen_publications:
                seen_publications.add(fingerprint)
                publications.append(publication)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            # A retailer may publish several current/upcoming editions. One
            # broken viewer must not suppress its other valid editions.
            continue
    return publications


async def fetch_all_publications(*, client: httpx.AsyncClient | None = None) -> list[Publication]:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=25,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 BaggerShopping/0.17", "Accept-Language": "da-DK,da;q=0.9"},
        )
    try:
        # Sources are independent upstream systems. Fetch them concurrently
        # and keep healthy publications when one retailer is unavailable or
        # changes its markup. Customer-facing validation happens per
        # publication in mobile_offers.
        tasks = [fetch_meny_flyer(client=client)] + [
            fetch_retailer_publications(source, client=client) for source in SOURCES
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        result: list[Publication] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                continue
            if isinstance(outcome, list):
                result.extend(outcome)
            else:
                result.append(outcome)
        order = {name.casefold(): index for index, name in enumerate(RETAILER_ORDER)}
        return sorted(result, key=lambda p: (order.get(p.retailer.casefold(), 99), p.status != "current", p.title.casefold()))
    finally:
        if owns_client:
            await client.aclose()

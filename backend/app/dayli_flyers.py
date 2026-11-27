"""Read the page images and offer zones published in MineTilbud's public reader."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from .flyer_intelligence import assess_quality, box_from_mapping, couple_offers, extract_variants
from .member_pricing import detect_member_pricing, has_membership_signal
from .meny_flyer import Offer, OfferVariant, Publication

INDEX = "https://minetilbud.dk/"
CONTENT_SOURCE = "dayli-public-flyer"
RETAILERS = {
    "365-discount": ("365discount", "coop-365.dayli.eu"),
    "rema1000": ("REMA 1000", "rema1000.aviou.io"),
    "netto": ("Netto", "netto.dayli.eu"),
}
RETAILER_NAMES = frozenset(name for name, _ in RETAILERS.values())
_TIMEZONE = ZoneInfo("Europe/Copenhagen")
_ISSUE_PATH = re.compile(r"/katalog/([a-z0-9-]+)/[a-z0-9-]+/?")
_IMAGE_PATH = re.compile(r"/[0-9-]+/pages/[^/]+\.(?:webp|jpe?g)")
_AMOUNT = r"(\d{1,4}(?:[,.]\d{2})?)"
_MEMBER_REBATE = re.compile(r"\bmedlems?[-\s]?rabat\s+" + _AMOUNT, re.IGNORECASE)
_NON_MEMBER_PRICE = re.compile(r"\bpris\s+ikke[-\s]?medlem\s+" + _AMOUNT, re.IGNORECASE)
_LOG = logging.getLogger(__name__)


class _ReaderLinks(HTMLParser):
    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: dict[str, str] = {}
        self.readers: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and (href := values.get("href")):
            url = urljoin(INDEX, href)
            parsed = urlsplit(url)
            match = _ISSUE_PATH.fullmatch(parsed.path)
            if parsed.netloc == "minetilbud.dk" and parsed.scheme == "https" and match and match[1] in RETAILERS:
                self.issues[url] = match[1]
        if tag == "av-catalogue" and (url := values.get("url")):
            self.readers.append(url)


def _number(value: object) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _objects(value: object) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _page_image(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.netloc == "pdf.dayli.se" and _IMAGE_PATH.fullmatch(parsed.path):
        return value
    return None


def _offer(publication: Publication, page_number: int, page: dict, zone: dict) -> Offer | None:
    entry = {**_mapping(zone.get("data")), **_mapping(zone.get("overrides"))}
    heading = _text(entry.get("title")).rstrip("* ")
    price = _number(entry.get("price"))
    if not heading or price is None or not 0 < price <= 10_000:
        return None

    size = _mapping(page.get("image_size"))
    width, height = _number(size.get("width")), _number(size.get("height"))
    values = {key: _number(entry.get(key)) for key in ("x", "y", "width", "height")}
    if not width or not height or any(value is None for value in values.values()):
        return None
    box = box_from_mapping(
        {key: value / (width if key in {"x", "width"} else height) for key, value in values.items()},
        source="dayli-zone",
    )
    if box is None:
        return None

    description = _text(entry.get("description"))
    notes = [_text(entry.get(key)) for key in ("price_note", "before_price_note")]
    raw_text = " ".join(part for part in (heading, description, *notes) if part)
    rebate = _MEMBER_REBATE.search(raw_text)
    non_member_price = _NON_MEMBER_PRICE.search(raw_text)
    if rebate and non_member_price:
        ordinary = float(non_member_price[1].replace(",", "."))
        discount = float(rebate[1].replace(",", "."))
        if discount > 0 and math.isclose(ordinary - discount, price, abs_tol=0.005):
            raw_text += f" Medlemspris {price:.2f} kr. Ikke-medlemspris {ordinary:.2f} kr."
    if (rebate or non_member_price or has_membership_signal(raw_text)) and detect_member_pricing(
        retailer=publication.retailer, price=price, normal_price=None, text=raw_text,
    ) is None:
        # Do not present an unresolved membership price as an ordinary price.
        price = None

    identity = _text(zone.get("uuid")) or hashlib.sha256(
        f"{publication.id}|{page_number}|{heading}|{box.x}|{box.y}".encode()
    ).hexdigest()[:20]
    variant_names = [_text(value) for value in _text(entry.get("variants")).split(";")]
    candidates = extract_variants(identity, heading, description, payload={"variants": variant_names})
    quality = assess_quality(
        heading=heading, raw_text=raw_text, price=price, box=box, variants=candidates,
        structured=True, has_crop=False,
    )
    brand = _text(entry.get("brand"))
    return Offer(
        id=f"{identity}-{page_number}", retailer=publication.retailer,
        publication_id=publication.id, publication_title=publication.title,
        valid_from=publication.valid_from, valid_until=publication.valid_until,
        product_name=heading, brand=brand if brand and ";" not in brand else None,
        price=price, source_url=publication.source_url, page_number=page_number,
        image_url=publication.page_image_urls[page_number - 1],
        hotspot_x=box.x, hotspot_y=box.y, hotspot_width=box.width, hotspot_height=box.height,
        raw_text=raw_text, safe_to_add=price is not None and quality.score >= 0.60,
        variants=[OfferVariant(id=value.id, name=value.name, description=description or None) for value in candidates],
        hotspot_confidence=quality.hotspot_confidence, variant_confidence=quality.variant_confidence,
        quality_score=quality.score, quality_source=CONTENT_SOURCE,
        quality_issues=quality.issues, quality_signals=quality.signals,
    )


def parse_publication(payload: dict, retailer: str, issue_url: str, *, today: date) -> Publication | None:
    variables = _mapping(payload.get("variables"))
    issuer = _mapping(variables.get("author"))
    if (
        _text(issuer.get("name")).casefold() != retailer.casefold()
        or variables.get("publication_type") != "pdf"
        or variables.get("currency") != "DKK"
    ):
        return None
    dates = _mapping(variables.get("offers"))
    provider_id = str(variables.get("publication_id", ""))
    try:
        start, end = date.fromisoformat(dates["start"]), date.fromisoformat(dates["expiration"])
    except (KeyError, TypeError, ValueError):
        return None
    if not provider_id.isdecimal() or end < max(start, today):
        return None

    pages: list[dict] = []
    for section in _objects(payload.get("content")):
        if section.get("kind") != "pdf-section":
            continue
        section_pages = [
            row for flip in _objects(section.get("content_items")) if flip.get("kind") == "pageflip"
            for row in _objects(flip.get("content_items")) if row.get("kind") == "pages"
        ]
        if len(section_pages) != _mapping(section.get("data")).get("pages"):
            return None
        pages.extend(section_pages)
    if not pages or len(pages) > 150:
        return None

    images: list[str] = []
    for index, item in enumerate(pages, start=1):
        page = _mapping(item.get("overrides"))
        image = _page_image(page.get("zoom_image_url_webp")) or _page_image(page.get("zoom_image_url"))
        if page.get("page_no") != index or image is None:
            return None
        images.append(image)

    publication = Publication(
        id=f"dayli-{provider_id}", retailer=retailer, title=f"{retailer} tilbudsavis",
        valid_from=start.strftime("%d.%m.%Y"), valid_until=end.strftime("%d.%m.%Y"),
        status="upcoming" if today < start else "current", page_count=len(images),
        page_image_urls=images, reader_url=issue_url, reader_kind="page-images",
        source_url=issue_url, content_source=CONTENT_SOURCE,
    )
    publication.structured_offers = couple_offers([
        offer
        for index, item in enumerate(pages, start=1)
        for zone in _objects(item.get("content_items")) if zone.get("kind") == "zone"
        if (offer := _offer(publication, index, _mapping(item.get("overrides")), zone)) is not None
    ])
    return publication


async def fetch_publications(client: httpx.AsyncClient) -> list[Publication]:
    try:
        index = await client.get(INDEX)
        index.raise_for_status()
    except httpx.HTTPError as exc:
        _LOG.warning("MineTilbud offer catalogue index unavailable (%s)", type(exc).__name__)
        return []

    today = datetime.now(_TIMEZONE).date()

    async def fetch_one(issue_url: str, slug: str) -> Publication | None:
        retailer, reader_host = RETAILERS[slug]
        try:
            issue = await client.get(issue_url)
            issue.raise_for_status()
            for url in _ReaderLinks(issue.text).readers:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or parsed.netloc != reader_host or not re.fullmatch(r"/[a-z0-9-]+/?", parsed.path):
                    continue
                feed = await client.get(f"https://{reader_host}{parsed.path.rstrip('/')}/feed.json")
                feed.raise_for_status()
                publication = parse_publication(_mapping(feed.json()), retailer, issue_url, today=today)
                if publication is not None:
                    return publication
        except (httpx.HTTPError, ValueError) as exc:
            _LOG.warning("%s public flyer unavailable (%s)", retailer, type(exc).__name__)
        return None

    publications = await asyncio.gather(*(
        fetch_one(url, slug) for url, slug in _ReaderLinks(index.text).issues.items()
    ))
    return list({item.id: item for item in publications if item is not None}.values())

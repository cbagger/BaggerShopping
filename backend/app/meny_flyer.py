from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser
from typing import Iterable

import httpx
from pydantic import BaseModel


MENY_FLYER_URL = "https://ugensavis.meny.dk/"
WEEK_RE = re.compile(r"MENY\s+uge\s+(?P<week>\d{2})(?P<year>\d{2})", re.IGNORECASE)
VALIDITY_RE = re.compile(
    r"Avisen\s+g[æa]lder\s+fra\s+(?:[a-zæøå]+\s+)?(?P<from>\d{2}\.\d{2}\.\d{4})"
    r"\s+til\s+og\s+med\s+(?:[a-zæøå]+\s+)?(?P<until>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)


class MenyFlyerPublication(BaseModel):
    ok: bool = True
    retailer: str = "MENY"
    title: str
    week: int | None = None
    year: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_url: str
    text: str
    page_count: int = 0
    content_source: str = "visible-html"


class MenyFlyerSearchResult(BaseModel):
    ok: bool = True
    retailer: str = "MENY"
    query: str
    publication: MenyFlyerPublication
    matches: list[str]


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
            return
        if lowered in {"style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "script":
            self._in_script = False
            return
        if lowered in {"style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_script:
            if data.strip():
                self.script_parts.append(data)
            return
        if self._skip_depth:
            return
        compact = " ".join(data.split())
        if compact:
            self.visible_parts.append(compact)


def _normalize_space(value: str) -> str:
    return " ".join(
        value.replace("\u00ad", "")
        .replace("\u200b", "")
        .replace("\\u0027", "'")
        .replace("\\u0026", "&")
        .split()
    )


def _json_array_from_marker(source: str, marker: str) -> list[str]:
    """Extract a JSON string array following marker using balanced brackets.

    iPaper currently serializes viewer state either as ordinary JSON-ish
    JavaScript or one escaped JSON layer. This helper deliberately understands
    both forms without depending on the rest of the viewer implementation.
    """
    start = source.find(marker)
    if start < 0:
        return []
    start = source.find("[", start + len(marker))
    if start < 0:
        return []

    escaped = marker.startswith('\\"')
    depth = 0
    in_string = False
    slash_count = 0
    end = None
    for index in range(start, len(source)):
        char = source[index]
        if char == "\\":
            slash_count += 1
            continue
        is_escaped = slash_count % 2 == 1
        slash_count = 0
        if char == '"' and not is_escaped:
            in_string = not in_string
        if in_string:
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return []

    payload = source[start:end]
    attempts = [payload]
    if escaped or '\\"' in payload:
        attempts.append(payload.replace('\\"', '"'))
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [_normalize_space(str(item)) for item in parsed if str(item).strip()]
    return []


def _extract_ipaper_page_texts(raw_html: str, scripts: list[str]) -> list[str]:
    sources = [raw_html, html_lib.unescape(raw_html), *scripts]
    markers = ('"pageTexts":', '\\"pageTexts\\":')
    for source in sources:
        for marker in markers:
            pages = _json_array_from_marker(source, marker)
            if pages:
                return pages
    return []


def parse_meny_flyer_html(html: str, source_url: str = MENY_FLYER_URL) -> MenyFlyerPublication:
    parser = _FlyerHTMLParser()
    parser.feed(html)

    visible_text = _normalize_space(" ".join(parser.visible_parts))
    script_text = _normalize_space(" ".join(parser.script_parts))
    metadata_text = _normalize_space(f"{visible_text} {script_text}")

    title_match = WEEK_RE.search(metadata_text)
    title = title_match.group(0) if title_match else "MENY ugens avis"
    week = int(title_match.group("week")) if title_match else None
    year = 2000 + int(title_match.group("year")) if title_match else None

    validity = VALIDITY_RE.search(metadata_text)
    valid_from = validity.group("from") if validity else None
    valid_until = validity.group("until") if validity else None

    page_texts = _extract_ipaper_page_texts(html, parser.script_parts)
    if page_texts:
        content_text = _normalize_space(" ".join(page_texts))
        content_source = "ipaper-pageTexts"
    else:
        content_text = visible_text
        content_source = "visible-html"

    return MenyFlyerPublication(
        title=title,
        week=week,
        year=year,
        valid_from=valid_from,
        valid_until=valid_until,
        source_url=source_url,
        text=content_text,
        page_count=len(page_texts),
        content_source=content_source,
    )


def _windows(tokens: list[str], query: str, radius: int = 16) -> Iterable[str]:
    needle = query.casefold()
    for index, token in enumerate(tokens):
        if needle not in token.casefold():
            continue
        start = max(0, index - radius)
        end = min(len(tokens), index + radius + 1)
        yield _normalize_space(" ".join(tokens[start:end]))


def search_publication(publication: MenyFlyerPublication, query: str) -> MenyFlyerSearchResult:
    query = query.strip()
    if not query:
        raise ValueError("Query cannot be empty")

    tokens = publication.text.split(" ")
    matches: list[str] = []
    seen: set[str] = set()
    for match in _windows(tokens, query):
        key = match.casefold()
        if key in seen:
            continue
        seen.add(key)
        matches.append(match)

    return MenyFlyerSearchResult(query=query, publication=publication, matches=matches)


async def fetch_meny_flyer(*, client: httpx.AsyncClient | None = None) -> MenyFlyerPublication:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 BaggerShopping/0.4 MENY-flyer-PoC",
                "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
            },
        )
    try:
        response = await client.get(MENY_FLYER_URL)
        response.raise_for_status()
        return parse_meny_flyer_html(response.text, str(response.url))
    finally:
        if owns_client:
            await client.aclose()


async def search_live_meny_flyer(query: str, *, client: httpx.AsyncClient | None = None) -> MenyFlyerSearchResult:
    publication = await fetch_meny_flyer(client=client)
    return search_publication(publication, query)

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable

import httpx
from pydantic import BaseModel


MENY_FLYER_URL = "https://ugensavis.meny.dk/"
WEEK_RE = re.compile(r"MENY\s+uge\s+(?P<week>\d{2})(?P<year>\d{2})", re.IGNORECASE)
VALIDITY_RE = re.compile(
    r"Avisen\s+g[æa]lder\s+fra\s+fredag\s+(?P<from>\d{2}\.\d{2}\.\d{4})\s+til\s+og\s+med\s+torsdag\s+(?P<until>\d{2}\.\d{2}\.\d{4})",
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


class MenyFlyerSearchResult(BaseModel):
    ok: bool = True
    retailer: str = "MENY"
    query: str
    publication: MenyFlyerPublication
    matches: list[str]


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        compact = " ".join(data.split())
        if compact:
            self.parts.append(compact)


def _normalize_space(value: str) -> str:
    return " ".join(value.replace("\u00ad", "").replace("\u200b", "").split())


def parse_meny_flyer_html(html: str, source_url: str = MENY_FLYER_URL) -> MenyFlyerPublication:
    parser = _VisibleTextParser()
    parser.feed(html)
    text = _normalize_space(" ".join(parser.parts))

    title_match = WEEK_RE.search(text)
    title = title_match.group(0) if title_match else "MENY ugens avis"
    week = int(title_match.group("week")) if title_match else None
    year = 2000 + int(title_match.group("year")) if title_match else None

    validity = VALIDITY_RE.search(text)
    valid_from = validity.group("from") if validity else None
    valid_until = validity.group("until") if validity else None

    return MenyFlyerPublication(
        title=title,
        week=week,
        year=year,
        valid_from=valid_from,
        valid_until=valid_until,
        source_url=source_url,
        text=text,
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

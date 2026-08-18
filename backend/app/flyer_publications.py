from __future__ import annotations

import asyncio
import json

import httpx

from . import flyer_adapters as raw
from .luna_overlay import apply_cached_enrichment
from .member_pricing_sources import (
    enrich_ipaper_offers,
    enrich_schwarz_publication,
    enrich_tjek_offers,
)
from .meny_flyer import Publication


RETAILER_ORDER = raw.RETAILER_ORDER


def parse_tjek_hotspots(
    publication: Publication,
    rows: object,
    offer_rows: object = None,
):
    """Parse Tjek geometry and preserve provider-owned member-price context."""
    offers = raw.parse_tjek_hotspots(publication, rows, offer_rows)
    return enrich_tjek_offers(offers, rows, offer_rows)


def publication_from_schwarz(
    payload: dict,
    source: raw.RetailerSource,
    reader_url: str,
) -> Publication:
    publication = raw._publication_from_schwarz(payload, source, reader_url)
    return enrich_schwarz_publication(publication, payload)


async def _fetch_meny_publication(*, client: httpx.AsyncClient) -> Publication:
    publication = await raw.fetch_meny_flyer(client=client)
    publication.structured_offers = enrich_ipaper_offers(
        publication,
        publication.structured_offers,
    )
    return publication


async def fetch_retailer_publications(
    source: raw.RetailerSource,
    *,
    client: httpx.AsyncClient,
) -> list[Publication]:
    """Fetch one retailer through the explicit provider-enrichment pipeline."""
    landing: httpx.Response | None = None
    try:
        landing = await client.get(source.landing_url)
        landing.raise_for_status()
    except httpx.HTTPError:
        if not source.tjek_dealer_id:
            raise

    landing_html = landing.text if landing is not None else ""
    landing_url = str(landing.url) if landing is not None else source.landing_url
    links = raw.discover_flyer_links(landing_html, source)

    if source.tjek_dealer_id:
        links = []

    if not source.tjek_dealer_id and raw.extract_embedded_page_images(landing_html):
        links.insert(0, raw.FlyerLink(landing_url, f"{source.retailer} tilbudsavis"))

    publications: list[Publication] = []
    seen_publications: set[tuple[str, ...]] = set()
    tjek_ids = raw._tjek_ids(landing_html)

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
                    str(row["id"])
                    for row in catalog_rows
                    if isinstance(row, dict) and row.get("id")
                )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            pass

    for catalog_id in dict.fromkeys(tjek_ids):
        try:
            metadata_response, pages_response, hotspots_response = await asyncio.gather(
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}"),
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/pages?w=700"),
                client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/hotspots"),
            )
            metadata_response.raise_for_status()
            pages_response.raise_for_status()
            publication = raw._publication_from_tjek(
                metadata_response.json(),
                pages_response.json(),
                source,
                landing_url,
            )
            if hotspots_response.is_success:
                detailed_rows = await raw._fetch_tjek_offer_rows(client, catalog_id)
                publication.structured_offers = parse_tjek_hotspots(
                    publication,
                    hotspots_response.json(),
                    detailed_rows,
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
                publication = publication_from_schwarz(
                    api_response.json(),
                    source,
                    link.url,
                )
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

            publication = raw._publication_from_html(response.text, source, link)
            if not publication.page_count:
                for catalog_id in raw._tjek_ids(response.text):
                    metadata_response, pages_response = await asyncio.gather(
                        client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}"),
                        client.get(f"https://squid-api.tjek.com/v2/catalogs/{catalog_id}/pages?w=700"),
                    )
                    metadata_response.raise_for_status()
                    pages_response.raise_for_status()
                    candidate = raw._publication_from_tjek(
                        metadata_response.json(),
                        pages_response.json(),
                        source,
                        link.url,
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

            publication.structured_offers = enrich_ipaper_offers(
                publication,
                raw.parse_enrichment_chunks(publication, chunks),
            )
            fingerprint = tuple(publication.page_image_urls)
            usable_spar = not (
                source.retailer == "SPAR"
                and publication.page_count <= 1
                and not publication.valid_until
            )
            if publication.page_count and usable_spar and fingerprint not in seen_publications:
                seen_publications.add(fingerprint)
                publications.append(publication)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            continue

    return publications


async def fetch_raw_publications(*, client: httpx.AsyncClient | None = None) -> list[Publication]:
    """Fetch deterministic provider publications without the cached Luna overlay."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=25,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 BaggerShopping/0.17",
                "Accept-Language": "da-DK,da;q=0.9",
            },
        )

    try:
        tasks = [_fetch_meny_publication(client=client)] + [
            fetch_retailer_publications(source, client=client)
            for source in raw.SOURCES
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

        order = {
            name.casefold(): index
            for index, name in enumerate(RETAILER_ORDER)
        }
        return sorted(
            result,
            key=lambda publication: (
                order.get(publication.retailer.casefold(), 99),
                publication.status != "current",
                publication.title.casefold(),
            ),
        )
    finally:
        if owns_client:
            await client.aclose()


async def fetch_all_publications(*, client: httpx.AsyncClient | None = None) -> list[Publication]:
    """Return customer-ready publications with verified cached Luna enrichment."""
    publications = await fetch_raw_publications(client=client)
    try:
        return apply_cached_enrichment(publications)
    except Exception:
        # AI cache/telemetry must never make deterministic flyer data unavailable.
        return publications


__all__ = [
    "RETAILER_ORDER",
    "fetch_all_publications",
    "fetch_raw_publications",
    "fetch_retailer_publications",
    "parse_tjek_hotspots",
    "publication_from_schwarz",
]

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta

import httpx

from . import flyer_adapters as raw
from .luna_overlay import apply_cached_enrichment
from .member_pricing_sources import (
    enrich_ipaper_offers,
    enrich_schwarz_publication,
    enrich_tjek_offers,
)
from .meny_flyer import Offer, Publication
from .retailer_sources import RETAILER_ORDER, SOURCES


def _tjek_offer_validity(rows: object) -> dict[str, tuple[str | None, str | None]]:
    """Extract authoritative per-offer validity from Tjek's detailed offer feed.

    A catalogue can be current while a campaign on one page starts later. Tjek
    exposes those campaign dates on detailed offer rows; keeping them separate
    from publication dates avoids presenting a Sunday-only offer on Saturday.
    """

    if not isinstance(rows, list):
        return {}
    result: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        valid_from = raw._iso_date(
            row.get("run_from")
            or row.get("valid_from")
            or row.get("start_at")
            or row.get("start_date")
        )
        valid_until = raw._iso_date(
            row.get("run_till")
            or row.get("valid_until")
            or row.get("end_at")
            or row.get("end_date")
        )
        if valid_from or valid_until:
            result[str(row["id"])] = (valid_from, valid_until)
    return result


def _apply_tjek_offer_validity(offers: list[Offer], rows: object) -> list[Offer]:
    validity = _tjek_offer_validity(rows)
    if not validity:
        return offers

    result: list[Offer] = []
    for offer in offers:
        # Tjek hotspot ids are stored as "<offer-id>-<page>" in the common
        # model. Split only the final numeric page suffix so ids containing
        # dashes remain intact.
        source_id, separator, page_suffix = offer.id.rpartition("-")
        if not separator or not page_suffix.isdigit():
            source_id = offer.id
        valid_from, valid_until = validity.get(source_id, (None, None))
        if not valid_from and not valid_until:
            result.append(offer)
            continue

        updates: dict = {}
        if valid_from:
            updates["valid_from"] = valid_from
        if valid_until:
            updates["valid_until"] = valid_until
        updates["quality_signals"] = list(dict.fromkeys([
            *offer.quality_signals,
            "provider-offer-validity",
        ]))
        result.append(offer.model_copy(update=updates))
    return result


def parse_tjek_hotspots(
    publication: Publication,
    rows: object,
    offer_rows: object = None,
):
    """Parse Tjek geometry and preserve provider-owned member-price context."""
    offers = raw.parse_tjek_hotspots(publication, rows, offer_rows)
    offers = _apply_tjek_offer_validity(offers, offer_rows)
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
            for source in SOURCES
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


def _parse_publication_date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value or "", "%d.%m.%Y").date()
    except ValueError:
        return None


def _meny_week_validity(publication: Publication) -> tuple[str | None, str | None]:
    """Infer MENY's Friday-through-Thursday window from its ISO week label.

    iPaper occasionally rotates to the next MENY release before the visible
    validity sentence is present in the reader HTML. The source publication ID
    and Luna/readiness fingerprint stay untouched; this is customer-serving
    metadata only and therefore cannot create duplicate paid Luna work.
    """
    if publication.retailer.casefold() != "meny" or not publication.week or not publication.year:
        return publication.valid_from, publication.valid_until
    try:
        monday = date.fromisocalendar(int(publication.year), int(publication.week), 1)
    except (TypeError, ValueError):
        return publication.valid_from, publication.valid_until
    inferred_from = (monday - timedelta(days=3)).strftime("%d.%m.%Y")
    inferred_until = (monday + timedelta(days=3)).strftime("%d.%m.%Y")
    return publication.valid_from or inferred_from, publication.valid_until or inferred_until


def _normalize_customer_publication(
    publication: Publication,
    *,
    today: date | None = None,
) -> Publication:
    """Repair serving-only validity/status without changing source identity."""
    today = today or date.today()
    valid_from, valid_until = _meny_week_validity(publication)
    start = _parse_publication_date(valid_from)
    end = _parse_publication_date(valid_until)

    status = publication.status
    if start is not None and today < start:
        status = "upcoming"
    elif end is not None and today > end:
        status = "expired"
    elif start is not None or end is not None:
        status = "current"

    updates: dict = {}
    if valid_from != publication.valid_from:
        updates["valid_from"] = valid_from
    if valid_until != publication.valid_until:
        updates["valid_until"] = valid_until
    if status != publication.status:
        updates["status"] = status

    if publication.retailer.casefold() == "meny" and (valid_from or valid_until):
        offers: list[Offer] = []
        offers_changed = False
        for offer in publication.structured_offers:
            offer_updates: dict = {}
            if valid_from and not offer.valid_from:
                offer_updates["valid_from"] = valid_from
            if valid_until and not offer.valid_until:
                offer_updates["valid_until"] = valid_until
            if offer_updates:
                offer = offer.model_copy(update=offer_updates)
                offers_changed = True
            offers.append(offer)
        if offers_changed:
            updates["structured_offers"] = offers

    return publication.model_copy(update=updates) if updates else publication


def _customer_ready_publications(
    publications: list[Publication],
    source_publications: list[Publication],
    *,
    today: date | None = None,
) -> list[Publication]:
    """Drop superseded MENY snapshots and normalize customer-visible dates.

    MENY/iPaper invalidates the previous release's signed CDN URLs as soon as
    the reader rotates. A verified serving-cache row for that old release is
    therefore not a safe stale-while-revalidate bridge. Only the MENY release
    that is still present in the live deterministic source may reach Mobile API.
    Other retailers keep the existing stable-cache behavior.
    """
    live_meny_ids = {
        publication.id
        for publication in source_publications
        if publication.retailer.casefold() == "meny"
    }
    result: list[Publication] = []
    for publication in publications:
        if publication.retailer.casefold() == "meny":
            if not live_meny_ids or publication.id not in live_meny_ids:
                continue
        normalized = _normalize_customer_publication(publication, today=today)
        if normalized.status != "expired":
            result.append(normalized)
    return result


async def fetch_all_publications(*, client: httpx.AsyncClient | None = None) -> list[Publication]:
    """Return customer-ready publications with verified cached Luna enrichment."""
    source_publications = await fetch_raw_publications(client=client)
    try:
        publications = apply_cached_enrichment(source_publications)
    except Exception:
        # AI cache/telemetry must never make deterministic flyer data unavailable.
        publications = source_publications
    return _customer_ready_publications(publications, source_publications)


__all__ = [
    "RETAILER_ORDER",
    "SOURCES",
    "fetch_all_publications",
    "fetch_raw_publications",
    "fetch_retailer_publications",
    "parse_tjek_hotspots",
    "publication_from_schwarz",
]

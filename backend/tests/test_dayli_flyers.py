from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx
import pytest

from app import dayli_flyers, flyer_publications, luna_semantic_engine, mobile_offers
from app.offer_serialization import customer_offer_payload


def _feed(retailer: str) -> dict:
    today = date.today()
    return {
        "variables": {
            "author": {"name": retailer},
            "publication_id": "17",
            "publication_type": "pdf",
            "currency": "DKK",
            "offers": {
                "start": today.isoformat(),
                "expiration": (today + timedelta(days=5)).isoformat(),
            },
        },
        "content": [{
            "kind": "pdf-section",
            "data": {"pages": 1},
            "content_items": [{
                "kind": "pageflip",
                "content_items": [{
                    "kind": "pages",
                    "overrides": {
                        "page_no": 1,
                        "zoom_image_url_webp": "https://pdf.dayli.se/17-2/pages/abc_1.webp",
                        "image_size": {"width": 600, "height": 1000},
                    },
                    "content_items": [{
                        "kind": "zone",
                        "uuid": "abc",
                        "data": {"title": "Bacon", "price": 45, "x": 15, "y": 100, "width": 280, "height": 300},
                        "overrides": {"description": "500 g. Medlems-rabat 10,-. Pris ikke-medlem 55,00."},
                    }],
                }],
            }],
        }],
    }


def test_public_flyer_uses_full_pages_and_existing_luna_and_mobile_offer_paths(monkeypatch):
    retailer = "365discount"
    issue_url = "https://minetilbud.dk/katalog/365-discount/uge-49/"
    feed_url = "https://coop-365.dayli.eu/uge-49/feed.json"
    requested = []

    def route(request):
        url = str(request.url)
        requested.append(url)
        if url == dayli_flyers.INDEX:
            return httpx.Response(200, text=f'<a href="{issue_url}">Avis</a>')
        if url == issue_url:
            return httpx.Response(200, text='<av-catalogue url="https://coop-365.dayli.eu/uge-49"></av-catalogue>')
        if url == feed_url:
            return httpx.Response(200, json=_feed(retailer))
        raise AssertionError(url)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            return await dayli_flyers.fetch_publications(client)

    (publication,) = asyncio.run(run())
    assert requested == [dayli_flyers.INDEX, issue_url, feed_url]
    assert publication.reader_kind == "page-images"
    assert publication.page_count == 1
    assert publication.page_image_urls[0].endswith("abc_1.webp")
    (offer,) = publication.structured_offers
    assert (offer.page_number, offer.hotspot_x, offer.hotspot_y, offer.safe_to_add) == (1, 0.025, 0.1, True)
    assert offer.image_url == publication.page_image_urls[0]
    serialized = customer_offer_payload(offer)
    assert serialized["price"] == 55
    assert serialized["member_price"] == 45

    monkeypatch.setattr(luna_semantic_engine, "load_store", lambda: {})
    monkeypatch.setattr(luna_semantic_engine, "load_config", lambda: {})
    (candidate,) = luna_semantic_engine.collect_page_audit_candidates([publication])
    assert candidate.offers == (offer,)
    assert candidate.image_url == publication.page_image_urls[0]

    async def catalogue():
        return [publication]

    monkeypatch.setattr(mobile_offers, "_publications", catalogue)
    response = asyncio.run(mobile_offers.publications())
    assert response["external_flyers"] == []
    assert response["publications"][0]["page_count"] == 1
    (visible_offer,) = asyncio.run(mobile_offers.publication_offers(publication.id))["offers"]
    assert visible_offer["member_price"] == 45
    assert visible_offer["hotspot_x"] == 0.025


@pytest.mark.parametrize("retailer", dayli_flyers.RETAILER_NAMES)
def test_public_flyer_rejects_an_incomplete_page_manifest(retailer):
    data = _feed(retailer)
    data["content"][0]["data"]["pages"] = 2
    assert dayli_flyers.parse_publication(data, retailer, dayli_flyers.INDEX, today=date.today()) is None


def test_pauses_only_the_rejecting_tjek_host():
    hosts = []

    def route(request):
        hosts.append(request.url.host)
        return httpx.Response(401 if request.url.host == "squid-api.tjek.com" else 200)

    async def run():
        flyer_publications._tjek_unauthorized_hosts.clear()
        try:
            async with flyer_publications._PublicationClient(transport=httpx.MockTransport(route)) as client:
                denied = await asyncio.gather(
                    *(client.get("https://squid-api.tjek.com/v2/catalogs") for _ in range(5)),
                    return_exceptions=True,
                )
                assert sum(isinstance(result, httpx.RequestError) for result in denied) == 4
                assert (await client.get(dayli_flyers.INDEX)).status_code == 200
        finally:
            flyer_publications._tjek_unauthorized_hosts.clear()

    asyncio.run(run())
    assert hosts == ["squid-api.tjek.com", "minetilbud.dk"]

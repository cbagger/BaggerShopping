import asyncio

from app import mobile_offers
from app.meny_flyer import parse_enrichment_chunks, parse_meny_flyer_html


def test_publication_offers_returns_current_publication_when_client_id_is_stale(monkeypatch):
    publication = parse_meny_flyer_html(
        '<p>MENY uge 3326</p><p>Avisen gælder fra fredag 07.08.2026 til og med torsdag 13.08.2026.</p>',
        source_url="https://ugensavis.meny.dk/?redirect=second-request",
    )
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13,
        "pageIndex": 0,
        "productId": "milk",
        "name": "Kakaomælk",
        "alttext": "Kakaomælk",
        "desc": "1 l",
        "price": 9.95,
    }]}])

    async def current_publication():
        return publication

    monkeypatch.setattr(mobile_offers, "_publication", current_publication)
    response = asyncio.run(mobile_offers.publication_offers("id-from-first-request"))

    assert response["publication"]["id"] == publication.id
    assert [offer["product_name"] for offer in response["offers"]] == ["Kakaomælk"]


def test_current_offers_static_route_returns_live_offer_count(monkeypatch):
    publication = parse_meny_flyer_html("<p>MENY uge 3326</p>")
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "milk", "name": "Kakaomælk",
        "alttext": "Kakaomælk", "desc": "1 l", "price": 9.95,
    }]}])

    async def current_publication():
        return publication

    monkeypatch.setattr(mobile_offers, "_publication", current_publication)
    response = asyncio.run(mobile_offers.current_publication_offers())

    assert response["offer_count"] == 1
    assert response["offers"][0]["product_name"] == "Kakaomælk"
    assert response["coverage"] == {
        "offer_count": 1,
        "hotspot_count": 0,
        "pages_without_hotspots": [],
        "pages": [],
    }


def test_current_offers_reports_hotspot_coverage_per_page(monkeypatch):
    publication = parse_meny_flyer_html(
        '<script>window.staticSettings = {"pages":[1,2,3],"aws":{}};</script><p>MENY uge 3326</p>'
    )
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {
            "type": 13, "pageIndex": 0, "productId": "milk", "name": "Kakaomælk",
            "alttext": "Kakaomælk", "desc": "1 l", "price": 9.95,
            "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1,
        },
        {
            "type": 13, "pageIndex": 1, "productId": "rye", "name": "Rugbrød",
            "alttext": "Rugbrød", "desc": "500 g", "price": 18,
        },
    ]}])

    async def current_publication():
        return publication

    monkeypatch.setattr(mobile_offers, "_publication", current_publication)
    response = asyncio.run(mobile_offers.current_publication_offers())

    assert response["coverage"]["offer_count"] == 2
    assert response["coverage"]["hotspot_count"] == 1
    assert response["coverage"]["pages_without_hotspots"] == [2]
    assert response["coverage"]["pages"][1] == {"page_number": 2, "offer_count": 1, "hotspot_count": 0}
    assert response["coverage"]["pages"][2] == {"page_number": 3, "offer_count": 0, "hotspot_count": 0}

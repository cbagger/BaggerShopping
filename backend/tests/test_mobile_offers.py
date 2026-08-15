import asyncio
from datetime import date
from types import SimpleNamespace

from app import mobile_offers
from app.meny_flyer import parse_enrichment_chunks, parse_meny_flyer_html


def test_search_without_retailer_searches_all_current_publications(monkeypatch):
    publications = []
    for retailer in ("MENY", "Bilka"):
        publication = parse_meny_flyer_html("<p>MENY uge 3326</p>")
        publication.retailer = retailer
        publication.status = "current"
        publications.append(publication)

    async def all_publications():
        return publications

    def search(publication, query):
        return SimpleNamespace(offers=[])

    monkeypatch.setattr(mobile_offers, "_publications", all_publications)
    monkeypatch.setattr(mobile_offers, "search_publication", search)
    response = asyncio.run(mobile_offers.search_offers(q="mælk", retailer=None))

    assert response["retailer"] == "Alle butikker"
    assert response["publication"] is None


def test_search_accepts_comma_separated_retailer_filter(monkeypatch):
    publications = []
    for retailer in ("MENY", "Bilka", "Lidl"):
        publication = parse_meny_flyer_html("<p>MENY uge 3326</p>")
        publication.retailer = retailer
        publication.status = "current"
        publications.append(publication)

    async def all_publications():
        return publications

    searched = []
    for publication in publications:
        publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
            "type": 13, "pageIndex": 0, "productId": publication.retailer,
            "name": "Kakaomælk", "alttext": "Kakaomælk", "desc": "1 l", "price": 10,
        }]}])

    def match(query, offer):
        searched.append(offer.retailer)
        return None

    monkeypatch.setattr(mobile_offers, "_publications", all_publications)
    monkeypatch.setattr(mobile_offers, "_search_match_result", match)
    asyncio.run(mobile_offers.search_offers(q="mælk", retailer="Bilka,Lidl"))

    assert searched == ["Bilka", "Lidl"]


def test_search_includes_upcoming_schulstad_and_keeps_all_flyer_variants(monkeypatch):
    publication = parse_meny_flyer_html("<p>REMA 1000 uge 34</p>")
    publication.retailer = "REMA 1000"
    publication.status = "upcoming"
    publication.valid_from = "16.08.2026"
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 9, "productId": "rye", "name": "Schulstad Multikerner", "alttext": "Schulstad brød", "desc": "470 g", "price": 12},
        {"type": 13, "pageIndex": 9, "productId": "sandwich", "name": "Schulstad Sandwich", "alttext": "Schulstad brød", "desc": "700 g", "price": 12},
    ]}])

    async def all_publications():
        return [publication]

    monkeypatch.setattr(mobile_offers, "_publications", all_publications)
    response = asyncio.run(mobile_offers.search_offers(q="Schulstad", retailer=None))

    assert response["offer_count"] == 1
    assert response["offers"][0]["publication_status"] == "upcoming"
    assert [value["name"] for value in response["offers"][0]["variants"]] == [
        "Schulstad Multikerner", "Schulstad Sandwich",
    ]


def test_cola_family_search_keeps_campaign_choices_and_marks_relevant_variants(monkeypatch):
    publication = parse_meny_flyer_html("<p>Netto uge 34</p>")
    publication.retailer = "Netto"
    publication.status = "current"
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 0, "productId": "coke", "name": "Coca-Cola", "alttext": "Sodavand og drikkevarer", "desc": "1,5 l", "price": 10},
        {"type": 13, "pageIndex": 0, "productId": "pepsi", "name": "Pepsi Max", "alttext": "Sodavand og drikkevarer", "desc": "1,5 l", "price": 10},
        {"type": 13, "pageIndex": 0, "productId": "fanta", "name": "Fanta Orange", "alttext": "Sodavand og drikkevarer", "desc": "1,5 l", "price": 10},
    ]}])

    async def all_publications():
        return [publication]

    monkeypatch.setattr(mobile_offers, "_publications", all_publications)
    response = asyncio.run(mobile_offers.search_offers(q="cola", retailer=None))

    variants = response["offers"][0]["variants"]
    assert [value["name"] for value in variants] == ["Coca-Cola", "Pepsi Max", "Fanta Orange"]
    assert [value["name"] for value in variants if value["matches_query"]] == ["Coca-Cola", "Pepsi Max"]


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
        "average_quality": 0.638,
        "manual_review_count": 1,
        "pages_requiring_review": [1],
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
    assert response["coverage"]["pages"][1] == {
        "page_number": 2, "offer_count": 1, "hotspot_count": 0, "review_count": 1,
    }
    assert response["coverage"]["pages"][2] == {
        "page_number": 3, "offer_count": 0, "hotspot_count": 0, "review_count": 0,
    }


def test_health_only_fails_when_flyer_is_not_functionally_usable():
    publication = parse_meny_flyer_html(
        '<script>window.staticSettings = {"pages":[1],"aws":{"url":"https://cdn.test"}};</script>'
        '<p>MENY uge 3326</p><p>Avisen gælder fra mandag 10.08.2026 til og med søndag 16.08.2026.</p>'
    )
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "milk", "name": "Kakaomælk",
        "alttext": "Kakaomælk", "price": 9.95,
        "x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1,
    }]}])

    assert mobile_offers._health_problems(publication, today=date(2026, 8, 11)) == []
    assert "avisen er udløbet" in mobile_offers._health_problems(publication, today=date(2026, 8, 17))


def test_health_rejects_material_hotspot_loss_but_not_empty_editorial_pages():
    publication = parse_meny_flyer_html(
        '<script>window.staticSettings = {"pages":[1,2],"aws":{"url":"https://cdn.test"}};</script>'
        '<p>MENY uge 3326</p><p>Avisen gælder fra mandag 10.08.2026 til og med søndag 16.08.2026.</p>'
    )
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "milk", "name": "Kakaomælk",
        "alttext": "Kakaomælk", "price": 9.95,
    }]}])

    problems = mobile_offers._health_problems(publication, today=date(2026, 8, 11))
    assert problems == ["kun 0/1 tilbud har markør"]


def test_reader_accepts_complete_pages_without_structured_offer_metadata():
    publication = parse_meny_flyer_html(
        '<script>window.staticSettings = {"pages":[1,2],"aws":{"url":"https://cdn.test"}};</script>'
        '<p>MENY uge 3326</p>'
    )
    assert mobile_offers._reader_problems(publication, today=date(2026, 8, 11)) == []
    assert mobile_offers._publication_payload(publication)["searchable"] is False

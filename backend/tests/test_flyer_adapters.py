import asyncio
from datetime import date

import httpx

from app.flyer_adapters import (
    RETAILER_ORDER,
    SOURCES,
    RetailerSource,
    discover_flyer_links,
    extract_embedded_page_images,
    extract_ipaper_minipaper,
    parse_tjek_hotspots,
    fetch_retailer_publications,
    fetch_all_publications,
    validity_from_text,
)
from app.meny_flyer import Publication


SOURCE = RetailerSource("Bilka", "https://www.bilka.dk/bilkaavisen/", ("avis.bilka.dk",))


def test_every_requested_retailer_has_an_adapter():
    assert RETAILER_ORDER == ("MENY", "365discount", "REMA 1000", "Bilka", "føtex", "Lidl", "Netto", "SPAR")
    assert {source.retailer for source in SOURCES} == set(RETAILER_ORDER) - {"MENY"}
    assert {source.retailer for source in SOURCES if source.tjek_dealer_id} == {
        "365discount", "REMA 1000", "Bilka", "føtex", "Lidl", "Netto", "SPAR",
    }


def test_discovers_current_and_upcoming_official_publications_without_week_rules():
    links = discover_flyer_links(
        """
        <a href="https://avis.bilka.dk/bilka/aviser/bilka-2026/uge-33-food/">
          Denne uges Bilka avis - Fødevarer Gælder fra d. 7. august til og med d. 13. august
        </a>
        <a href="https://avis.bilka.dk/bilka/aviser/bilka-2026/uge-34-food/">
          Næste uges Bilka avis - Fødevarer Gælder fra d. 14. august til og med d. 20. august
        </a>
        <a href="/tilbud-og-kampagner/tilbud/">Andre tilbud</a>
        """,
        SOURCE,
    )

    assert [link.url for link in links] == [
        "https://avis.bilka.dk/bilka/aviser/bilka-2026/uge-33-food/",
        "https://avis.bilka.dk/bilka/aviser/bilka-2026/uge-34-food/",
    ]


def test_reads_danish_text_and_numeric_validity_ranges():
    assert validity_from_text(
        "Gælder fra fredag d. 7. august til og med torsdag d. 13. august",
        today=date(2026, 8, 12),
    ) == ("07.08.2026", "13.08.2026")
    assert validity_from_text("08.08.2026 - 15.08.2026") == ("08.08.2026", "15.08.2026")


def test_fetches_and_normalizes_ipaper_publication_and_offers():
    landing = """
    <a href="https://avis.bilka.dk/current/">
      Bilka avis Gælder fra d. 7. august til og med d. 13. august
    </a>
    """
    viewer = """
    <script>
    window.staticSettings = {
      "pages":[1],
      "aws":{"url":"https://cdn.example"},
      "enrichments":{"chunkUrls":{"0":"https://cdn.example/chunk.json"}}
    };
    </script>
    """
    chunk = {"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "milk", "name": "Sødmælk",
        "alttext": "Sødmælk", "desc": "1 l", "price": 12,
        "x": 0.1, "y": 0.2, "width": 0.2, "height": 0.1,
    }]}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SOURCE.landing_url:
            return httpx.Response(200, text=landing)
        if str(request.url) == "https://avis.bilka.dk/current/":
            return httpx.Response(200, text=viewer)
        if str(request.url) == "https://cdn.example/chunk.json":
            return httpx.Response(200, json=chunk)
        return httpx.Response(404)

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_retailer_publications(SOURCE, client=client)

    publications = asyncio.run(fetch())

    assert len(publications) == 1
    assert publications[0].retailer == "Bilka"
    assert publications[0].valid_until == "13.08.2026"
    assert publications[0].page_image_urls == ["https://cdn.example/Pages/1/Normal.jpg"]
    assert publications[0].structured_offers[0].retailer == "Bilka"
    assert publications[0].structured_offers[0].product_name == "Sødmælk"


def test_discovers_embedded_official_reader():
    source = RetailerSource("SPAR", "https://spar.dk/ugensavis", ("ugensavis.spar.dk",))
    links = discover_flyer_links(
        '<iframe title="Ugens avis" src="https://ugensavis.spar.dk/MiniPaperFrame.aspx?PA=1"></iframe>',
        source,
    )
    assert [link.url for link in links] == ["https://ugensavis.spar.dk/MiniPaperFrame.aspx?PA=1"]


def test_extracts_all_signed_tjek_pages_in_order_and_prefers_largest_copy():
    html = """
    <img src="https://image-transformer-api.tjek.com/?u=uploads%2Fx%2Fp-2.webp&amp;w=700&amp;s=two">
    <img src="https://image-transformer-api.tjek.com/?u=uploads%2Fx%2Fp-1.webp&amp;w=250&amp;s=small">
    <img src="https://image-transformer-api.tjek.com/?u=uploads%2Fx%2Fp-1.webp&amp;w=700&amp;s=large">
    """
    assert extract_embedded_page_images(html) == [
        "https://image-transformer-api.tjek.com/?u=uploads%2Fx%2Fp-1.webp&w=700&s=large",
        "https://image-transformer-api.tjek.com/?u=uploads%2Fx%2Fp-2.webp&w=700&s=two",
    ]


def test_extracts_complete_spar_minipaper_not_only_preloaded_first_pages():
    html = r'''<script>start({"paperSettings":{"numberOfPages":3},
    "aws":{"policy":"token=abc\u0026expires=123","url":"https://cdn.ipaper.io/Papers/x/"}})</script>'''
    pages, _ = extract_ipaper_minipaper(html)
    assert pages == [
        "https://cdn.ipaper.io/Papers/x/Pages/1/Normal.jpg?token=abc&expires=123",
        "https://cdn.ipaper.io/Papers/x/Pages/2/Normal.jpg?token=abc&expires=123",
        "https://cdn.ipaper.io/Papers/x/Pages/3/Normal.jpg?token=abc&expires=123",
    ]


def test_fetches_tjek_catalogs_for_365_rema_and_netto():
    for retailer, slug, dealer in (("365discount", "365", "DWZE1w"), ("REMA 1000", "rema", "11deC"), ("Netto", "netto", "9ba51")):
        source = RetailerSource(retailer, f"https://{slug}.test/avis", (f"{slug}.test",), tjek_dealer_id=dealer)

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == source.landing_url:
                return httpx.Response(200, text="<h1>Ugens avis</h1>")
            if request.url.path == "/v2/catalogs" and request.url.params.get("dealer_id") == dealer:
                return httpx.Response(200, json=[{"id": "catalog1"}])
            if request.url.path == "/v2/catalogs/catalog1":
                return httpx.Response(200, json={
                    "id": "catalog1", "label": "Uge 33",
                    "run_from": "2026-08-08T22:00:00+0000",
                    "run_till": "2026-08-15T21:59:59+0000",
                })
            if request.url.path == "/v2/catalogs/catalog1/pages":
                return httpx.Response(200, json=[
                    {"view": "https://image-transformer-api.tjek.com/p-1.webp"},
                    {"view": "https://image-transformer-api.tjek.com/p-2.webp"},
                ])
            return httpx.Response(404)

        async def fetch():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await fetch_retailer_publications(source, client=client)

        publications = asyncio.run(fetch())
        assert len(publications) == 1
        assert publications[0].retailer == retailer
        assert publications[0].valid_from == "09.08.2026"
        assert publications[0].valid_until == "15.08.2026"
        assert publications[0].page_count == 2


def test_fetches_complete_lidl_flyer_from_schwarz_api():
    source = RetailerSource("Lidl", "https://lidl.test/aviser", ("lidl.test",))
    flyer_id = "019fb848-75bf-75c9-95d5-477c1069ddaf"
    landing = f'''<a href="https://lidl.test/current" data-track-id="{flyer_id}">
    Fra søndag 9.8 til 15.8. Tilbudsavis</a>'''

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == source.landing_url:
            return httpx.Response(200, text=landing)
        if request.url.host == "endpoints.leaflets.schwarz":
            assert request.url.params.get("flyer_identifier") == flyer_id
            return httpx.Response(200, json={"flyer": {
                "id": flyer_id, "name": "Fra søndag 9.8 til 15.8.",
                "startDate": "2026-08-09", "endDate": "2026-08-15",
                "pages": [{"image": "https://imgproxy.leaflets.schwarz/page-01.jpg"},
                          {"image": "https://imgproxy.leaflets.schwarz/page-02.jpg"}],
            }})
        return httpx.Response(404)

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_retailer_publications(source, client=client)

    publications = asyncio.run(fetch())
    assert len(publications) == 1
    assert publications[0].reader_kind == "schwarz-pages"
    assert publications[0].page_count == 2


def test_tjek_hotspots_become_searchable_positioned_offers():
    publication = Publication(
        id="catalog", retailer="Netto", title="Uge 33", source_url="https://netto.test",
        valid_from="08.08.2026", valid_until="15.08.2026", page_count=1,
        page_image_urls=["https://images.test/page.webp"],
    )
    offers = parse_tjek_hotspots(publication, [{
        "type": "offer", "id": "cola", "heading": "Coca-Cola eller Fanta",
        "locations": {"1": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.8], [0.1, 0.8]]},
        "offer": {"id": "cola", "heading": "Coca-Cola eller Fanta", "description": "24 x 33 cl",
                  "pricing": {"price": 69},
                  "quantity": {"unit": {"symbol": "cl"}, "size": {"from": 33}}},
    }])
    assert len(offers) == 1
    assert offers[0].retailer == "Netto"
    assert offers[0].price == 69
    assert offers[0].safe_to_add is True
    assert [variant.name for variant in offers[0].variants] == ["Coca-Cola", "Fanta"]
    assert offers[0].page_number == 1
    assert offers[0].hotspot_x == 0.1
    assert 0 < offers[0].hotspot_height < 1


def test_tjek_hotspots_prefer_nested_product_variants_over_campaign_heading():
    publication = Publication(
        id="catalog", retailer="365discount", title="Uge 33", source_url="https://365.test",
        valid_from="06.08.2026", valid_until="12.08.2026", page_count=1,
        page_image_urls=["https://images.test/page.webp"],
    )
    offers = parse_tjek_hotspots(publication, [{
        "type": "offer", "locations": {"1": [[0.1, 0.2], [0.4, 0.2], [0.4, 0.6]]},
        "offer": {
            "id": "tun", "heading": "Xtra! tun", "pricing": {"price": 5},
            "products": [{"name": "Xtra! tun i vand"}, {"title": "Xtra! tun i olie"}],
        },
    }])
    assert [variant.name for variant in offers[0].variants] == ["Xtra! tun i vand", "Xtra! tun i olie"]


def test_tjek_offer_feed_enriches_matching_hotspot_by_offer_id():
    publication = Publication(
        id="catalog", retailer="365discount", title="Uge 33", source_url="https://365.test",
        valid_from="06.08.2026", valid_until="12.08.2026", page_count=1,
        page_image_urls=["https://images.test/page.webp"],
    )
    hotspots = [{
        "type": "offer", "locations": {"1": [[0.1, 0.2], [0.4, 0.2], [0.4, 0.6]]},
        "offer": {"id": "tun", "heading": "Xtra! tun*", "pricing": {"price": 5}},
    }]
    details = [{
        "id": "tun", "heading": "Xtra! tun*",
        "description": "56 g. Kg-pris 89,29. Frit valg. 1 stk.",
        "pricing": {"price": 5, "pre_price": 8},
        "quantity": {"unit": {"symbol": "g"}, "size": {"from": 56}},
        "images": {"view": "https://images.test/tun-crop.webp"},
    }]

    offer = parse_tjek_hotspots(publication, hotspots, details)[0]

    assert offer.normal_price == 8
    assert offer.quantity == 56
    assert offer.unit == "g"
    assert offer.image_url == "https://images.test/tun-crop.webp"
    assert "Frit valg" in offer.raw_text


def test_one_broken_retailer_does_not_hide_healthy_publications(monkeypatch):
    from app import flyer_adapters

    meny = Publication(id="meny", retailer="MENY", title="MENY", source_url="https://meny.test")
    bilka = Publication(id="bilka", retailer="Bilka", title="Bilka", source_url="https://bilka.test")

    async def fake_meny(*, client):
        return meny

    async def fake_retailer(source, *, client):
        if source.retailer == "365discount":
            raise httpx.HTTPError("upstream unavailable")
        return [bilka] if source.retailer == "Bilka" else []

    monkeypatch.setattr(flyer_adapters, "fetch_meny_flyer", fake_meny)
    monkeypatch.setattr(flyer_adapters, "fetch_retailer_publications", fake_retailer)

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            return await fetch_all_publications(client=client)

    assert [publication.id for publication in asyncio.run(fetch())] == ["meny", "bilka"]


def test_one_broken_viewer_does_not_hide_another_publication():
    source = RetailerSource("Bilka", "https://bilka.test/aviser", ("avis.bilka.test",))
    landing = """
    <a href="https://avis.bilka.test/broken">Bilka avis 01.08.2026 - 07.08.2026</a>
    <a href="https://avis.bilka.test/current">Bilka avis 08.08.2026 - 15.08.2026</a>
    """
    viewer = '<script>window.staticSettings = {"pages":[1],"aws":{"url":"https://cdn.test"}};</script>'

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == source.landing_url:
            return httpx.Response(200, text=landing)
        if str(request.url) == "https://avis.bilka.test/broken":
            return httpx.Response(503)
        if str(request.url) == "https://avis.bilka.test/current":
            return httpx.Response(200, text=viewer)
        return httpx.Response(404)

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_retailer_publications(source, client=client)

    publications = asyncio.run(fetch())
    assert len(publications) == 1
    assert publications[0].source_url == "https://avis.bilka.test/current"

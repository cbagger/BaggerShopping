import asyncio

import httpx

from app.flyer_publications import RETAILER_ORDER, SOURCES, fetch_retailer_publications


EXPECTED = {
    "SuperBrugsen": "0b1e8",
    "Kvickly": "c1edq",
    "Brugsen": "d311fg",
    "Min Købmand": "603dfL",
    "LET-KØB": "f6f54",
}


def test_five_new_retailers_are_customer_visible_and_tjek_backed():
    assert RETAILER_ORDER[-5:] == tuple(EXPECTED)
    sources = {source.retailer: source for source in SOURCES}
    for retailer, dealer_id in EXPECTED.items():
        assert retailer in sources
        assert sources[retailer].tjek_dealer_id == dealer_id


def test_each_new_retailer_uses_existing_tjek_catalog_pipeline():
    sources = {source.retailer: source for source in SOURCES}

    for retailer, dealer_id in EXPECTED.items():
        source = sources[retailer]

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == source.landing_url:
                return httpx.Response(200, text="<h1>Ugens avis</h1>")
            if request.url.host == "squid-api.tjek.com" and request.url.path == "/v2/catalogs":
                assert request.url.params.get("dealer_id") == dealer_id
                return httpx.Response(200, json=[{"id": "catalog-current"}])
            if request.url.path == "/v2/catalogs/catalog-current":
                return httpx.Response(200, json={
                    "id": "catalog-current",
                    "label": f"{retailer} uge 34",
                    "run_from": "2026-08-13T22:00:00+0000",
                    "run_till": "2026-08-20T21:59:59+0000",
                })
            if request.url.path == "/v2/catalogs/catalog-current/pages":
                return httpx.Response(200, json=[
                    {"view": "https://image-transformer-api.tjek.com/p-1.webp"},
                ])
            if request.url.path == "/v2/catalogs/catalog-current/hotspots":
                return httpx.Response(200, json=[{
                    "type": "offer",
                    "locations": {"1": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4]]},
                    "offer": {
                        "id": "milk",
                        "heading": "Mælk",
                        "pricing": {"price": 12},
                    },
                }])
            if request.url.host == "api.etilbudsavis.dk" and request.url.path == "/v2/offers":
                return httpx.Response(200, json=[{
                    "id": "milk",
                    "heading": "Mælk",
                    "description": "1 liter",
                    "pricing": {"price": 12, "pre_price": 15},
                    "images": {"view": "https://images.test/milk.webp"},
                }])
            return httpx.Response(404)

        async def fetch():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await fetch_retailer_publications(source, client=client)

        publications = asyncio.run(fetch())
        assert len(publications) == 1
        assert publications[0].retailer == retailer
        assert publications[0].page_count == 1
        assert publications[0].structured_offers[0].product_name == "Mælk"
        assert publications[0].structured_offers[0].normal_price == 15

import asyncio

import httpx

from app import flyer_push
from app.flyer_publications import RETAILER_ORDER, SOURCES, fetch_retailer_publications
from app.retailer_sources import RETAILER_ORDER as REGISTRY_ORDER
from app.retailer_sources import SOURCES as REGISTRY_SOURCES


EXPECTED_ALL = (
    "MENY",
    "365discount",
    "REMA 1000",
    "Bilka",
    "føtex",
    "Lidl",
    "Netto",
    "SPAR",
    "SuperBrugsen",
    "Brugsen",
    "Min Købmand",
    "LET-KØB",
)

EXPECTED_NEW = {
    "SuperBrugsen": "0b1e8",
    "Brugsen": "d311fg",
    "Min Købmand": "603dfL",
    "LET-KØB": "f6f54",
}


def test_all_twelve_retailers_share_one_customer_registry():
    assert RETAILER_ORDER == EXPECTED_ALL
    assert REGISTRY_ORDER == EXPECTED_ALL
    assert SOURCES == REGISTRY_SOURCES
    assert {source.retailer for source in SOURCES} == set(EXPECTED_ALL) - {"MENY"}
    assert flyer_push.RETAILER_ORDER == EXPECTED_ALL


def test_four_new_retailers_are_tjek_backed_first_class_sources():
    sources = {source.retailer: source for source in SOURCES}
    for retailer, dealer_id in EXPECTED_NEW.items():
        assert retailer in sources
        assert sources[retailer].tjek_dealer_id == dealer_id


def test_notification_retailer_endpoint_exposes_all_twelve():
    payload = asyncio.run(flyer_push.notification_retailers())
    assert payload == {"ok": True, "retailers": list(EXPECTED_ALL)}


def test_each_new_retailer_uses_existing_tjek_catalog_pipeline():
    sources = {source.retailer: source for source in SOURCES}

    for retailer, dealer_id in EXPECTED_NEW.items():
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


def test_shared_kvickly_flyer_is_not_registered_twice():
    assert "SuperBrugsen" in EXPECTED_ALL
    assert "Kvickly" not in EXPECTED_ALL
    assert all(source.retailer != "Kvickly" for source in SOURCES)

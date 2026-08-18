import asyncio

from app import flyer_adapters, flyer_publications
from app.meny_flyer import Offer, Publication


def _publication(*, retailer="føtex", identity="pub-1") -> Publication:
    return Publication(
        id=identity,
        retailer=retailer,
        title="Uge 34",
        valid_from="01.01.2099",
        valid_until="31.12.2099",
        status="current",
        source_url="https://example.test/flyer",
        page_count=1,
        page_image_urls=["https://example.test/page-1.jpg"],
    )


def test_tjek_pipeline_preserves_structured_member_price_context():
    publication = _publication()
    hotspots = [{
        "type": "offer",
        "offer": {"id": "offer-1"},
        "locations": {
            "1": [[0.10, 0.10], [0.40, 0.10], [0.40, 0.40], [0.10, 0.40]],
        },
    }]
    details = [{
        "id": "offer-1",
        "heading": "Salling Seafoodmix",
        "description": "150-300 g",
        "pricing": {"price": 29},
        "appPrice": 25,
    }]

    offers = flyer_publications.parse_tjek_hotspots(publication, hotspots, details)

    assert len(offers) == 1
    assert offers[0].price == 29
    assert "member price 25 kr" in offers[0].raw_text.casefold()


def test_meny_pipeline_enriches_local_page_membership_context(monkeypatch):
    publication = _publication(retailer="MENY", identity="meny-1")
    publication.page_texts = [
        "Quickbury Fastfood Buns MEDLEMSPRIS 9,95 almindelig pris 14. Kuponen aktiveres i appen."
    ]
    publication.structured_offers = [Offer(
        id="offer-1",
        retailer="MENY",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="Quickbury Fastfood Buns",
        price=14,
        source_url=publication.source_url,
        raw_text="Quickbury Fastfood Buns 14 kr",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        hotspot_confidence=0.95,
    )]

    async def fake_fetch_meny_flyer(*, client):
        return publication

    monkeypatch.setattr(flyer_adapters, "fetch_meny_flyer", fake_fetch_meny_flyer)

    result = asyncio.run(flyer_publications._fetch_meny_publication(client=object()))

    assert "[kurv-page-context]" in result.structured_offers[0].raw_text
    assert "medlemspris 9,95" in result.structured_offers[0].raw_text.casefold()


def test_public_fetch_applies_overlay_but_raw_fetch_contract_stays_separate(monkeypatch):
    publication = _publication(retailer="Netto")

    async def fake_raw(*, client=None):
        return [publication]

    seen = []

    def fake_overlay(publications):
        seen.append(publications)
        return [publication.model_copy(update={"title": "Luna customer generation"})]

    monkeypatch.setattr(flyer_publications, "fetch_raw_publications", fake_raw)
    monkeypatch.setattr(flyer_publications, "apply_cached_enrichment", fake_overlay)

    result = asyncio.run(flyer_publications.fetch_all_publications())

    assert result[0].title == "Luna customer generation"
    assert seen == [[publication]]


def test_public_fetch_fails_open_when_luna_overlay_is_unavailable(monkeypatch):
    publication = _publication(retailer="Netto")

    async def fake_raw(*, client=None):
        return [publication]

    def broken_overlay(_):
        raise RuntimeError("corrupt AI cache")

    monkeypatch.setattr(flyer_publications, "fetch_raw_publications", fake_raw)
    monkeypatch.setattr(flyer_publications, "apply_cached_enrichment", broken_overlay)

    result = asyncio.run(flyer_publications.fetch_all_publications())

    assert result == [publication]


def test_package_initialization_no_longer_replaces_provider_functions():
    assert flyer_adapters.parse_tjek_hotspots.__module__ == "app.flyer_adapters"
    assert flyer_adapters.fetch_all_publications.__module__ == "app.flyer_adapters"

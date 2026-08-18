import asyncio
import importlib

import app
from app import flyer_publications, flyer_push, mobile_offers
from app.meny_flyer import Offer, Publication


def _publication(title: str) -> Publication:
    publication = Publication(
        id=f"pub-{title}",
        retailer="Netto",
        title=title,
        valid_from="01.01.2099",
        valid_until="31.12.2099",
        status="current",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://example.test/page.jpg"],
    )
    publication.structured_offers = [Offer(
        id=f"offer-{title}",
        retailer="Netto",
        publication_id=publication.id,
        publication_title=title,
        product_name="Testvare",
        price=10,
        source_url=publication.source_url,
        raw_text="Testvare 10 kr",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        hotspot_confidence=0.99,
    )]
    return publication


def _reset_mobile_cache():
    mobile_offers._publication_cache = None
    mobile_offers._publication_cache_time = 0.0
    mobile_offers._publications_cache = []
    mobile_offers._publication_refresh_task = None
    mobile_offers._publication_readiness_revision = None


def test_readiness_change_invalidates_ram_and_loads_new_verified_generation(monkeypatch):
    _reset_mobile_cache()
    old = _publication("Old")
    new = _publication("New")
    mobile_offers._publications_cache = [old]
    mobile_offers._publication_cache_time = 999999999.0
    mobile_offers._publication_readiness_revision = "revision-old"

    monkeypatch.setattr(mobile_offers, "readiness_revision", lambda: "revision-new")
    monkeypatch.setattr(mobile_offers, "load_verified_publications", lambda: [new])
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: None)

    async def forbidden_fetch():
        raise AssertionError("verified disk generation should satisfy readiness refresh")

    monkeypatch.setattr(mobile_offers, "fetch_all_publications", forbidden_fetch)

    result = asyncio.run(mobile_offers._publications())

    assert [publication.title for publication in result] == ["New"]
    assert mobile_offers._publication_readiness_revision == "revision-new"
    assert [publication.title for publication in mobile_offers._publications_cache] == ["New"]


def test_flyer_push_import_does_not_wrap_mobile_publications():
    before = mobile_offers._publications

    importlib.reload(flyer_push)

    assert mobile_offers._publications is before
    assert flyer_push.fetch_all_publications is flyer_publications.fetch_raw_publications


def test_package_initializer_has_no_raw_fetch_compatibility_shim():
    assert not hasattr(app, "_original_fetch_all_publications")

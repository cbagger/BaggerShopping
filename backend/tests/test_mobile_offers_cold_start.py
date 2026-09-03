import asyncio
import json
import time

import httpx

from app import flyer_serving_reader, mobile_offers
from app.meny_flyer import Offer, Publication


def _publication(*, title="Uge 34", price=15.0, status="current") -> Publication:
    publication = Publication(
        id="cold-week",
        retailer="REMA 1000",
        title=title,
        valid_from="01.01.2099",
        valid_until="31.12.2099",
        status=status,
        source_url="https://example.test/rema",
        page_count=1,
        page_image_urls=["https://example.test/rema/page-1.jpg"],
    )
    publication.structured_offers = [Offer(
        id="offer-1",
        retailer="REMA 1000",
        publication_id=publication.id,
        publication_title=publication.title,
        product_name="Kohberg brød",
        price=price,
        source_url=publication.source_url,
        raw_text=f"Kohberg brød {price} kr",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        hotspot_confidence=0.95,
    )]
    return publication


def _signed_meny_publication(*, policy="expired", price=15.0) -> Publication:
    publication = _publication(title="MENY uge 0199", price=price, status="upcoming")
    old_url = f"https://cdn.test/meny/Pages/1/Normal.jpg?Policy={policy}&Signature={policy}"
    publication.id = "meny-cold-week"
    publication.retailer = "MENY"
    publication.source_url = "https://ugensavis.meny.dk/"
    publication.reader_url = publication.source_url
    publication.reader_kind = "embedded-viewer"
    publication.page_image_urls = [old_url]
    publication.structured_offers = [publication.structured_offers[0].model_copy(update={
        "retailer": "MENY",
        "publication_id": publication.id,
        "publication_title": publication.title,
        "source_url": publication.source_url,
        "image_url": old_url,
    })]
    return publication


def _reset_mobile_cache(monkeypatch):
    monkeypatch.setattr(mobile_offers, "_publication_cache", None)
    monkeypatch.setattr(mobile_offers, "_publication_cache_time", 0.0)
    monkeypatch.setattr(mobile_offers, "_publications_cache", [])
    monkeypatch.setattr(mobile_offers, "_publication_refresh_task", None)
    monkeypatch.setattr(mobile_offers, "_publication_readiness_revision", None)


def test_verified_serving_cache_reader_fails_closed_on_unverified_and_expired_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_SERVING_CACHE_PATH", str(tmp_path / "flyer-serving-cache.json"))

    verified = _publication(title="Verified")
    unverified = _publication(title="Unverified")
    expired = _publication(title="Expired", status="expired")

    def row(publication, *, verified_value):
        return {
            "fingerprint": publication.title,
            "verified": verified_value,
            "saved_at": 1,
            "publication": publication.model_dump(exclude={"text", "page_texts"}),
        }

    (tmp_path / "flyer-serving-cache.json").write_text(json.dumps({
        "version": 2,
        "publications": {
            "verified": row(verified, verified_value=True),
            "unverified": row(unverified, verified_value=False),
            "expired": row(expired, verified_value=True),
        },
    }), encoding="utf-8")

    loaded = flyer_serving_reader.load_verified_publications()

    assert [publication.title for publication in loaded] == ["Verified"]


def test_verified_serving_cache_reader_excludes_retired_retailers(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYER_SERVING_CACHE_PATH", str(tmp_path / "flyer-serving-cache.json"))
    retired = _publication(title="Shared Kvickly flyer")
    retired.retailer = "Kvickly"

    (tmp_path / "flyer-serving-cache.json").write_text(json.dumps({
        "version": 2,
        "publications": {
            "retired": {
                "fingerprint": "retired",
                "verified": True,
                "saved_at": 1,
                "publication": retired.model_dump(exclude={"text", "page_texts"}),
            },
        },
    }), encoding="utf-8")

    assert flyer_serving_reader.load_verified_publications() == []


def test_refresh_transient_meny_reader_urls_preserves_verified_offer_data():
    cached = _signed_meny_publication(policy="expired", price=19.95)
    html = """
    <script>
    window.staticSettings = {
      "pages":[1],
      "aws":{
        "url":"https://cdn.test/meny",
        "policy":"Policy=fresh&Signature=fresh"
      }
    };
    </script>
    <p>MENY uge 0199</p>
    <p>Avisen gælder fra fredag 01.01.2099 til og med torsdag 31.12.2099.</p>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ugensavis.meny.dk/"
        return httpx.Response(200, text=html, request=request)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await mobile_offers._refresh_transient_reader_urls_once([cached], client=client)

    refreshed = asyncio.run(run())

    assert refreshed is not None
    publication = refreshed[0]
    assert publication.id == cached.id
    assert publication.page_image_urls == [
        "https://cdn.test/meny/Pages/1/Normal.jpg?Policy=fresh&Signature=fresh"
    ]
    assert publication.structured_offers[0].price == 19.95
    assert publication.structured_offers[0].image_url == publication.page_image_urls[0]
    assert publication.structured_offers[0].hotspot_x == 0.1


def test_cold_start_refreshes_signed_meny_urls_before_first_response(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    cached = _signed_meny_publication(policy="expired")
    fresh = _signed_meny_publication(policy="fresh")
    monkeypatch.setattr(mobile_offers, "load_verified_publications", lambda: [cached])

    async def refresh_reader(publications, **_):
        assert publications == [cached]
        mobile_offers._replace_publication_cache([fresh])
        return [fresh]

    async def forbidden_provider_fetch():
        raise AssertionError("cold-start request must not wait for full provider fetch")

    monkeypatch.setattr(mobile_offers, "_refresh_transient_reader_urls_once", refresh_reader)
    monkeypatch.setattr(mobile_offers, "fetch_all_publications", forbidden_provider_fetch)
    scheduled = []
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: scheduled.append(True))

    result = asyncio.run(mobile_offers._publications())

    assert result[0].page_image_urls == fresh.page_image_urls
    assert mobile_offers._publications_cache[0].page_image_urls == fresh.page_image_urls
    assert scheduled == [True]


def test_cold_start_serves_verified_disk_cache_before_provider_fetch(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    cached = _publication(title="Disk cache")
    monkeypatch.setattr(mobile_offers, "load_verified_publications", lambda: [cached])

    async def forbidden_provider_fetch():
        raise AssertionError("cold-start request must not wait for provider fetch")

    monkeypatch.setattr(mobile_offers, "fetch_all_publications", forbidden_provider_fetch)
    scheduled = []
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: scheduled.append(True))

    result = asyncio.run(mobile_offers._publications())

    assert [publication.title for publication in result] == ["Disk cache"]
    assert scheduled == [True]
    assert mobile_offers._publications_cache[0].title == "Disk cache"


def test_stale_signed_meny_cache_refreshes_reader_before_response(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    cached = _signed_meny_publication(policy="expired")
    fresh = _signed_meny_publication(policy="fresh")
    mobile_offers._publications_cache = [cached]
    mobile_offers._publication_cache_time = time.monotonic() - mobile_offers._CACHE_TTL_SECONDS - 1

    monkeypatch.setattr(
        mobile_offers,
        "load_verified_publications",
        lambda: (_ for _ in ()).throw(AssertionError("disk should not be re-read while RAM cache is usable")),
    )

    async def refresh_reader(publications, **_):
        assert publications == [cached]
        mobile_offers._replace_publication_cache([fresh])
        return [fresh]

    monkeypatch.setattr(mobile_offers, "_refresh_transient_reader_urls_once", refresh_reader)
    scheduled = []
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: scheduled.append(True))

    result = asyncio.run(mobile_offers._publications())

    assert result[0].page_image_urls == fresh.page_image_urls
    assert scheduled == [True]


def test_stale_memory_cache_is_returned_while_single_refresh_is_scheduled(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    cached = _publication(title="Warm but stale")
    mobile_offers._publications_cache = [cached]
    mobile_offers._publication_cache_time = time.monotonic() - mobile_offers._CACHE_TTL_SECONDS - 1

    monkeypatch.setattr(
        mobile_offers,
        "load_verified_publications",
        lambda: (_ for _ in ()).throw(AssertionError("disk should not be re-read while RAM cache is usable")),
    )
    scheduled = []
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: scheduled.append(True))

    result = asyncio.run(mobile_offers._publications())

    assert result == [cached]
    assert scheduled == [True]


def test_background_refresh_atomically_replaces_cached_generation(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    old = _publication(title="Old", price=15)
    new = _publication(title="New", price=12)
    mobile_offers._publications_cache = [old]

    async def provider_fetch():
        return [new]

    monkeypatch.setattr(mobile_offers, "fetch_all_publications", provider_fetch)

    refreshed = asyncio.run(mobile_offers._refresh_publications_once())

    assert refreshed == [new]
    assert mobile_offers._publications_cache == [new]
    assert mobile_offers._publications_cache[0].structured_offers[0].price == 12


def test_failed_background_refresh_keeps_last_working_cache(monkeypatch):
    _reset_mobile_cache(monkeypatch)
    cached = _publication(title="Keep me")
    mobile_offers._publications_cache = [cached]

    async def provider_fetch():
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(mobile_offers, "fetch_all_publications", provider_fetch)

    refreshed = asyncio.run(mobile_offers._refresh_publications_once())

    assert refreshed is None
    assert mobile_offers._publications_cache == [cached]

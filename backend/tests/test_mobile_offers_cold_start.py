import asyncio
import json
import time

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
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        hotspot_confidence=0.95,
    )]
    return publication


def _reset_mobile_cache(monkeypatch):
    monkeypatch.setattr(mobile_offers, "_publication_cache", None)
    monkeypatch.setattr(mobile_offers, "_publication_cache_time", 0.0)
    monkeypatch.setattr(mobile_offers, "_publications_cache", [])
    monkeypatch.setattr(mobile_offers, "_publication_refresh_task", None)


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

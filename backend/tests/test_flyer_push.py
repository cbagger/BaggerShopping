import os
import asyncio
import time
from datetime import date, timedelta

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import flyer_push
from app import flyer_readiness as readiness
from app import mobile_offers
from app.flyer_readiness import mark_ready, publication_fingerprint, publication_is_ready
from app.meny_flyer import Offer, Publication
from app.mobile_main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-token"}


def publication(identifier: str, retailer: str = "MENY") -> Publication:
    valid_from = date.today()
    valid_until = valid_from + timedelta(days=6)
    return Publication(
        id=identifier, retailer=retailer, title="Uge 34", valid_from=valid_from.strftime("%d.%m.%Y"),
        valid_until=valid_until.strftime("%d.%m.%Y"), status="current", source_url="https://example.test",
        page_count=1, page_image_urls=["https://example.test/1.jpg"],
    )


def usable_publication(identifier: str) -> Publication:
    value = publication(identifier)
    offer = Offer(
        id=f"{identifier}-offer",
        retailer="MENY",
        publication_id=identifier,
        publication_title="Uge 34",
        product_name="Testvare",
        price=10,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        raw_text="Testvare 10 kr",
        hotspot_confidence=0.99,
        quality_score=0.99,
    )
    return value.model_copy(update={"structured_offers": [offer]})


def test_device_preferences_are_filtered_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    response = client.put("/api/mobile/v1/flyer-notifications/device", headers=HEADERS, json={
        "device_id": "12345678-1234-1234-1234-123456789012",
        "device_token": "ab" * 32,
        "retailers": ["MENY", "Ikke en butik"],
        "enabled": True,
        "environment": "sandbox",
    })
    assert response.status_code == 200
    assert response.json()["retailers"] == ["MENY"]
    assert flyer_push._load()["devices"]["12345678-1234-1234-1234-123456789012"]["environment"] == "sandbox"


def test_first_check_seeds_without_push_and_marks_baseline_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    existing = publication("existing")

    async def publications(): return [existing]
    async def fail_send(*args, **kwargs): raise AssertionError("must not send on seed")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", publications)
    monkeypatch.setattr(flyer_push, "_send", fail_send)
    assert asyncio.run(flyer_push.check_and_send()) == {"new": 0, "sent": 0, "failed": 0}
    assert publication_is_ready(existing) is True
    assert flyer_push._load()["seen_notification_releases"][existing.id] == flyer_push._publication_release_key(existing)


def test_new_publication_is_not_pushed_until_luna_marks_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    flyer_push._save({
        "initialized": True,
        "seen_publications": ["old"],
        "devices": {
            "meny-device": {"device_token": "aa" * 32, "retailers": ["MENY"], "enabled": True, "environment": "production"},
            "lidl-device": {"device_token": "bb" * 32, "retailers": ["Lidl"], "enabled": True, "environment": "production"},
        },
    })
    old = publication("old")
    new = publication("new")

    async def publications(): return [old, new]
    sent = []

    async def send(token, environment, title, body, publication_id, retailer):
        sent.append((token, body, publication_id, retailer)); return True

    monkeypatch.setattr(flyer_push, "fetch_all_publications", publications)
    monkeypatch.setattr(flyer_push, "_send", send)

    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 0, "failed": 0}
    assert sent == []
    assert publication_is_ready(new) is False
    assert "new" not in flyer_push._load()["seen_publications"]

    assert mark_ready(new) is True

    async def must_not_fetch():
        raise AssertionError("ready delivery must not refetch providers")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", must_not_fetch)
    delivery = asyncio.run(flyer_push.deliver_ready_notifications())
    assert delivery == {"ready": 1, "sent": 1, "failed": 0}
    assert sent == [("aa" * 32, "MENY Uge 34 er nu tilgængelig", "new", "MENY")]
    store = flyer_push._load()
    assert "new" in store["seen_publications"]
    assert store["seen_notification_releases"]["new"] == flyer_push._publication_release_key(new)


def test_same_publication_content_revision_is_gated_without_duplicate_push(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    original = publication("same", retailer="365discount")

    async def initial_publications(): return [original]
    async def never_send(*args, **kwargs): raise AssertionError("baseline must not send")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", initial_publications)
    monkeypatch.setattr(flyer_push, "_send", never_send)
    asyncio.run(flyer_push.check_and_send())

    store = flyer_push._load()
    store["devices"] = {
        "device": {
            "device_token": "aa" * 32,
            "retailers": ["365discount"],
            "enabled": True,
            "environment": "production",
        },
    }
    flyer_push._save(store)

    changed = original.model_copy(update={"page_image_urls": ["https://example.test/changed.jpg"]})
    sent = []

    async def changed_publications(): return [changed]
    async def send(*args): sent.append(args); return True

    monkeypatch.setattr(flyer_push, "fetch_all_publications", changed_publications)
    monkeypatch.setattr(flyer_push, "_send", send)

    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 0, "failed": 0}
    assert publication_is_ready(changed) is False

    assert mark_ready(changed) is True
    delivery = asyncio.run(flyer_push.deliver_ready_notifications())
    assert delivery == {"ready": 1, "sent": 0, "failed": 0}
    assert sent == []

    updated = flyer_push._load()
    assert updated["seen_publication_fingerprints"]["same"] == publication_fingerprint(changed)
    assert updated["seen_notification_releases"]["same"] == flyer_push._publication_release_key(original)


def test_same_publication_id_new_validity_window_notifies_once(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    original = publication("same")

    async def initial_publications(): return [original]
    async def never_send(*args, **kwargs): raise AssertionError("baseline must not send")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", initial_publications)
    monkeypatch.setattr(flyer_push, "_send", never_send)
    asyncio.run(flyer_push.check_and_send())

    store = flyer_push._load()
    store["devices"] = {
        "device": {
            "device_token": "aa" * 32,
            "retailers": ["MENY"],
            "enabled": True,
            "environment": "production",
        },
    }
    flyer_push._save(store)

    next_valid_from = date.today() + timedelta(days=7)
    next_valid_until = next_valid_from + timedelta(days=6)
    next_release = original.model_copy(update={
        "title": "Uge 35",
        "valid_from": next_valid_from.strftime("%d.%m.%Y"),
        "valid_until": next_valid_until.strftime("%d.%m.%Y"),
        "page_image_urls": ["https://example.test/week-35.jpg"],
    })
    sent = []

    async def changed_publications(): return [next_release]
    async def send(*args): sent.append(args); return True

    monkeypatch.setattr(flyer_push, "fetch_all_publications", changed_publications)
    monkeypatch.setattr(flyer_push, "_send", send)

    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 0, "failed": 0}
    assert mark_ready(next_release) is True

    delivery = asyncio.run(flyer_push.deliver_ready_notifications())
    assert delivery == {"ready": 1, "sent": 1, "failed": 0}
    assert len(sent) == 1
    assert "MENY Uge 35 er nu tilgængelig" in sent[0][3]
    assert asyncio.run(flyer_push.deliver_ready_notifications()) == {"ready": 0, "sent": 0, "failed": 0}


def test_legacy_push_store_migrates_known_release_without_restart_push(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    original = publication("same", retailer="365discount")
    readiness.observe_publications([original], bootstrap_ready_ids=None)

    flyer_push._save({
        "initialized": True,
        "seen_publications": ["same"],
        "seen_publication_fingerprints": {"same": publication_fingerprint(original)},
        "devices": {
            "device": {
                "device_token": "aa" * 32,
                "retailers": ["365discount"],
                "enabled": True,
                "environment": "production",
            },
        },
    })

    changed = original.model_copy(update={"page_image_urls": ["https://example.test/restart-mutated.jpg"]})
    sent = []

    async def publications(): return [changed]
    async def send(*args): sent.append(args); return True

    monkeypatch.setattr(flyer_push, "fetch_all_publications", publications)
    monkeypatch.setattr(flyer_push, "_send", send)

    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 0, "failed": 0}
    migrated = flyer_push._load()
    assert migrated["seen_notification_releases"]["same"] == flyer_push._publication_release_key(changed)

    assert mark_ready(changed) is True
    delivery = asyncio.run(flyer_push.deliver_ready_notifications())
    assert delivery == {"ready": 1, "sent": 0, "failed": 0}
    assert sent == []


def test_readiness_revision_invalidates_mobile_publication_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_READINESS_STORE_PATH", str(tmp_path / "readiness.json"))
    stale = usable_publication("stale")
    fresh = usable_publication("fresh")

    original_cache = list(mobile_offers._publications_cache)
    original_single = mobile_offers._publication_cache
    original_time = mobile_offers._publication_cache_time
    original_revision = mobile_offers._publication_readiness_revision

    mobile_offers._publications_cache = [stale]
    mobile_offers._publication_cache = stale
    mobile_offers._publication_cache_time = time.monotonic()
    mobile_offers._publication_readiness_revision = readiness.readiness_revision()

    async def fetch_fresh():
        return [fresh]

    monkeypatch.setattr(mobile_offers, "fetch_all_publications", fetch_fresh)
    monkeypatch.setattr(mobile_offers, "load_verified_publications", lambda: [fresh])
    monkeypatch.setattr(mobile_offers, "_schedule_publication_refresh", lambda: None)

    try:
        readiness.observe_publications([fresh], bootstrap_ready_ids=None)
        result = asyncio.run(mobile_offers._publications())
        assert [value.id for value in result] == ["fresh"]
    finally:
        mobile_offers._publications_cache = original_cache
        mobile_offers._publication_cache = original_single
        mobile_offers._publication_cache_time = original_time
        mobile_offers._publication_readiness_revision = original_revision

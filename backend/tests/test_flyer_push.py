import os
import asyncio

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import flyer_push
from app.flyer_readiness import mark_ready, publication_is_ready
from app.meny_flyer import Publication
from app.mobile_main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-token"}


def publication(identifier: str, retailer: str = "MENY") -> Publication:
    return Publication(
        id=identifier, retailer=retailer, title="Uge 34", valid_from="14.08.2026",
        valid_until="20.08.2026", status="current", source_url="https://example.test",
        page_count=1, page_image_urls=["https://example.test/1.jpg"],
    )


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

    # Provider detection creates a processing event, but push remains blocked.
    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 0, "failed": 0}
    assert sent == []
    assert publication_is_ready(new) is False
    assert "new" not in flyer_push._load()["seen_publications"]

    # Luna's publication-complete marker opens the API gate. Notification can
    # then be delivered from local readiness metadata without another provider
    # fetch, which is the production fast path.
    assert mark_ready(new) is True

    async def must_not_fetch():
        raise AssertionError("ready delivery must not refetch providers")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", must_not_fetch)
    delivery = asyncio.run(flyer_push.deliver_ready_notifications())
    assert delivery == {"ready": 1, "sent": 1, "failed": 0}
    assert sent == [("aa" * 32, "MENY Uge 34 er nu tilgængelig", "new", "MENY")]
    assert "new" in flyer_push._load()["seen_publications"]


def test_same_publication_id_changed_version_is_gated_and_can_notify_again(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    original = publication("same")

    async def initial_publications(): return [original]
    async def never_send(*args, **kwargs): raise AssertionError("baseline must not send")

    monkeypatch.setattr(flyer_push, "fetch_all_publications", initial_publications)
    monkeypatch.setattr(flyer_push, "_send", never_send)
    asyncio.run(flyer_push.check_and_send())

    changed = original.model_copy(update={
        "page_image_urls": ["https://example.test/changed.jpg"],
    })
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
    assert delivery == {"ready": 1, "sent": 0, "failed": 0}  # no configured targets

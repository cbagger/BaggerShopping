import os
import asyncio

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import flyer_push
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


def test_first_check_seeds_without_push(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    async def publications(): return [publication("existing")]
    async def fail_send(*args, **kwargs): raise AssertionError("must not send on seed")
    monkeypatch.setattr(flyer_push, "fetch_all_publications", publications)
    monkeypatch.setattr(flyer_push, "_send", fail_send)
    assert asyncio.run(flyer_push.check_and_send()) == {"new": 0, "sent": 0, "failed": 0}


def test_new_publication_only_pushes_to_selected_retailer(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    flyer_push._save({
        "initialized": True,
        "seen_publications": ["old"],
        "devices": {
            "meny-device": {"device_token": "aa" * 32, "retailers": ["MENY"], "enabled": True, "environment": "production"},
            "lidl-device": {"device_token": "bb" * 32, "retailers": ["Lidl"], "enabled": True, "environment": "production"},
        },
    })
    async def publications(): return [publication("old"), publication("new")]
    sent = []
    async def send(token, environment, title, body, publication_id):
        sent.append((token, body, publication_id)); return True
    monkeypatch.setattr(flyer_push, "fetch_all_publications", publications)
    monkeypatch.setattr(flyer_push, "_send", send)
    result = asyncio.run(flyer_push.check_and_send())
    assert result == {"new": 1, "sent": 1, "failed": 0}
    assert sent == [("aa" * 32, "MENY Uge 34 er nu tilgængelig", "new")]
    assert "new" in flyer_push._load()["seen_publications"]

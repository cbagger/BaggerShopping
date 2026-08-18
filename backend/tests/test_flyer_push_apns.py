import asyncio

from app import flyer_push


def _ready_record():
    return {
        "publication_id": "pub-new",
        "retailer": "365discount",
        "title": "Uge 34",
        "fingerprint": "fp-new",
        "valid_from": "20.08.2026",
        "valid_until": "26.08.2026",
    }


def _store():
    return {
        "initialized": True,
        "seen_publications": [],
        "seen_publication_fingerprints": {},
        "seen_notification_releases": {},
        "devices": {
            "dead-device": {
                "device_token": "aa" * 32,
                "retailers": ["365discount"],
                "enabled": True,
                "environment": "production",
            }
        },
    }


def test_apns_terminal_delivery_classification():
    assert flyer_push._apns_delivery_result(410, "Unregistered") == {
        "ok": False,
        "terminal": True,
        "reason": "Unregistered",
        "status_code": 410,
    }
    assert flyer_push._apns_delivery_result(400, "BadDeviceToken")["terminal"] is True
    assert flyer_push._apns_delivery_result(400, "DeviceTokenNotForTopic")["terminal"] is True
    assert flyer_push._apns_delivery_result(500, "InternalServerError")["terminal"] is False
    assert flyer_push._apns_delivery_result(429, "TooManyRequests")["terminal"] is False


def test_terminal_invalid_device_is_disabled_and_release_is_acknowledged(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    flyer_push._save(_store())
    sends = []

    async def terminal_send(*args, **kwargs):
        sends.append((args, kwargs))
        return {
            "ok": False,
            "terminal": True,
            "reason": "Unregistered",
            "status_code": 410,
        }

    monkeypatch.setattr(flyer_push, "_send", terminal_send)
    row = _ready_record()

    first = asyncio.run(flyer_push._deliver_ready_records([row]))

    assert first == {"ready": 1, "sent": 0, "failed": 1}
    assert len(sends) == 1

    store = flyer_push._load()
    device = store["devices"]["dead-device"]
    release_key = flyer_push._record_release_key(row)

    assert device["enabled"] is False
    assert device["disabled_reason"] == "apns:Unregistered"
    assert isinstance(device["disabled_at"], int)
    assert store["seen_publication_fingerprints"]["pub-new"] == "fp-new"
    assert store["seen_notification_releases"]["pub-new"] == release_key
    assert "pub-new" in store["seen_publications"]

    second = asyncio.run(flyer_push._deliver_ready_records([row]))

    assert second == {"ready": 0, "sent": 0, "failed": 0}
    assert len(sends) == 1


def test_transient_apns_failure_keeps_device_enabled_and_release_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    flyer_push._save(_store())
    sends = []

    async def transient_send(*args, **kwargs):
        sends.append((args, kwargs))
        return {
            "ok": False,
            "terminal": False,
            "reason": "InternalServerError",
            "status_code": 500,
        }

    monkeypatch.setattr(flyer_push, "_send", transient_send)
    row = _ready_record()

    first = asyncio.run(flyer_push._deliver_ready_records([row]))
    second = asyncio.run(flyer_push._deliver_ready_records([row]))

    assert first == {"ready": 1, "sent": 0, "failed": 1}
    assert second == {"ready": 1, "sent": 0, "failed": 1}
    assert len(sends) == 2

    store = flyer_push._load()
    device = store["devices"]["dead-device"]

    assert device["enabled"] is True
    assert "disabled_reason" not in device
    assert "pub-new" not in store.get("seen_publication_fingerprints", {})
    assert "pub-new" not in store.get("seen_notification_releases", {})


def test_successful_delivery_still_records_release_with_structured_result(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_PUSH_STORE_PATH", str(tmp_path / "push.json"))
    flyer_push._save(_store())

    async def successful_send(*args, **kwargs):
        return {
            "ok": True,
            "terminal": False,
            "reason": None,
            "status_code": 200,
        }

    monkeypatch.setattr(flyer_push, "_send", successful_send)
    row = _ready_record()

    result = asyncio.run(flyer_push._deliver_ready_records([row]))
    store = flyer_push._load()
    release_key = flyer_push._record_release_key(row)

    assert result == {"ready": 1, "sent": 1, "failed": 0}
    assert release_key in store["devices"]["dead-device"]["delivered_publication_releases"]
    assert store["seen_notification_releases"]["pub-new"] == release_key

import json
import os
from types import SimpleNamespace

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")
os.environ.setdefault("SAMSUNG_LIST_ID", "test-list")

from fastapi.testclient import TestClient

import app.mobile_main as mobile
from app.samsung import SamsungFoodClient


client = TestClient(mobile.app)
AUTH = {"Authorization": "Bearer test-token"}


def test_name_bound_offer_metadata_promotes_once_to_samsung_item_id(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    payload = {
        "item_name": "Hamburger Buns 6-pak",
        "retailer": "MENY",
        "price": 14.0,
        "valid_from": "14.08.2026",
        "valid_until": "20.08.2026",
        "offer_id": "buns-offer",
        "publication_id": "meny-current",
        "matched_item_name": "Hamburger Buns 6-pak",
        "offer_snapshot": None,
    }
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=payload).status_code == 200

    calls = 0

    async def fake_get_list(self):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            items=[SimpleNamespace(id="item-123", name="Hamburger Buns 6-pak")]
        )

    monkeypatch.setattr(SamsungFoodClient, "get_list", fake_get_list)

    first = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert first.status_code == 200
    row = first.json()["metadata"][0]
    assert row["item_id"] == "item-123"
    assert row["item_name"] == "Hamburger Buns 6-pak"
    assert row["offer_id"] == "buns-offer"
    assert calls == 1

    persisted = json.loads(store_path.read_text("utf-8"))
    assert list(persisted) == ["item:item-123"]

    async def must_not_read_again(self):
        raise AssertionError("ID-bound metadata must not trigger another Samsung list read")

    monkeypatch.setattr(SamsungFoodClient, "get_list", must_not_read_again)
    second = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert second.status_code == 200
    assert second.json()["metadata"][0]["item_id"] == "item-123"


def test_name_only_remove_can_delete_an_id_bound_record(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))
    store_path.write_text(
        json.dumps({
            "item:item-123": {
                "item_name": "Hamburger Buns 6-pak",
                "item_id": "item-123",
                "retailer": "MENY",
                "price": 14.0,
                "valid_from": None,
                "valid_until": None,
                "offer_id": "buns-offer",
                "publication_id": "meny-current",
                "matched_item_name": "Hamburger Buns 6-pak",
                "offer_snapshot": None,
            }
        }),
        encoding="utf-8",
    )

    removed = client.post(
        "/api/mobile/v1/offer-metadata/remove",
        headers=AUTH,
        json={"item_name": "Hamburger Buns 6-pak"},
    )
    assert removed.status_code == 200
    assert removed.json() == {"ok": True, "removed": True}
    assert json.loads(store_path.read_text("utf-8")) == {}

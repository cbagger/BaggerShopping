import os
from types import SimpleNamespace

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")
os.environ.setdefault("SAMSUNG_LIST_ID", "test-list")

from fastapi.testclient import TestClient

import app.mobile_main as mobile
from app.samsung import SamsungFoodClient


client = TestClient(mobile.app)
AUTH = {"Authorization": "Bearer test-token"}


def use_store(monkeypatch, tmp_path):
    path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(path))
    return path


def test_shared_offer_metadata_requires_mobile_token(monkeypatch, tmp_path):
    use_store(monkeypatch, tmp_path)
    response = client.get("/api/mobile/v1/offer-metadata")
    assert response.status_code == 401


def test_offer_metadata_round_trips_all_shared_fields(monkeypatch, tmp_path):
    store_path = use_store(monkeypatch, tmp_path)
    payload = {
        "item_name": "Letmælk",
        "retailer": "MENY",
        "price": 12.95,
        "valid_from": "10.08.2026",
        "valid_until": "16.08.2026",
        "offer_id": "offer-123",
        "publication_id": "meny-uge-33",
        "matched_item_name": "Letmælk",
    }

    put = client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=payload)
    assert put.status_code == 200
    assert store_path.exists()

    get = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert get.status_code == 200
    assert get.json() == {"ok": True, "metadata": [payload]}


def test_migration_sync_adds_missing_local_metadata_but_server_wins_conflicts(monkeypatch, tmp_path):
    use_store(monkeypatch, tmp_path)
    server_record = {
        "item_name": "Kaffe",
        "retailer": "Bilka",
        "price": 49.0,
        "valid_from": "07.08.2026",
        "valid_until": "13.08.2026",
        "offer_id": "server-offer",
        "publication_id": "bilka-current",
        "matched_item_name": "Kaffe",
    }
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=server_record).status_code == 200

    local_conflict = dict(server_record, retailer="MENY", price=39.0, offer_id="old-local-offer")
    local_missing = {
        "item_name": "Rugbrød",
        "retailer": "MENY",
        "price": 18.0,
        "valid_from": "10.08.2026",
        "valid_until": "16.08.2026",
        "offer_id": "bread-offer",
        "publication_id": "meny-current",
        "matched_item_name": "Rugbrød",
    }

    response = client.put(
        "/api/mobile/v1/offer-metadata/sync",
        headers=AUTH,
        json={"metadata": [local_conflict, local_missing]},
    )
    assert response.status_code == 200
    records = {record["item_name"]: record for record in response.json()["metadata"]}
    assert records["Kaffe"] == server_record
    assert records["Rugbrød"] == local_missing


def test_offer_metadata_can_be_updated_and_removed(monkeypatch, tmp_path):
    use_store(monkeypatch, tmp_path)
    initial = {
        "item_name": "Smør",
        "retailer": "MENY",
        "price": 20.0,
        "valid_from": None,
        "valid_until": "16.08.2026",
        "offer_id": "butter-1",
        "publication_id": "meny-current",
        "matched_item_name": "Smør",
    }
    updated = dict(initial, retailer="Bilka", price=18.0, offer_id="butter-2")

    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=initial).status_code == 200
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=updated).status_code == 200

    get = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert get.json()["metadata"] == [updated]

    remove = client.post(
        "/api/mobile/v1/offer-metadata/remove",
        headers=AUTH,
        json={"item_name": "  SMØR  "},
    )
    assert remove.status_code == 200
    assert remove.json() == {"ok": True, "removed": True}
    assert client.get("/api/mobile/v1/offer-metadata", headers=AUTH).json()["metadata"] == []


def test_item_rename_updates_samsung_payload_and_moves_offer_metadata(monkeypatch, tmp_path):
    use_store(monkeypatch, tmp_path)
    original = {
        "item_name": "Gammel mælk",
        "retailer": "365discount",
        "price": 10.0,
        "valid_from": "10.08.2026",
        "valid_until": "16.08.2026",
        "offer_id": "milk-offer",
        "publication_id": "365-current",
        "matched_item_name": "Gammel mælk",
    }
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=original).status_code == 200

    def fake_init(self):
        self.list_id = "test-list"

    async def fake_find_item(self, item_id):
        assert item_id == "item-123"
        return SimpleNamespace(
            name="Gammel mælk",
            checked=True,
            quantity=3.0,
            unit="stk",
        )

    async def fake_post_sync_items(self, body):
        payload = body[5:]
        assert "Ny mælk".encode("utf-8") in payload
        assert b"item123" in payload
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "__init__", fake_init)
    monkeypatch.setattr(SamsungFoodClient, "_find_item", fake_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)

    response = client.patch(
        "/api/mobile/v1/items/item-123/name",
        headers=AUTH,
        json={"name": "  Ny   mælk  "},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Ny mælk"
    assert response.json()["old_name"] == "Gammel mælk"
    assert response.json()["offer_metadata_migrated"] is True

    records = client.get("/api/mobile/v1/offer-metadata", headers=AUTH).json()["metadata"]
    assert records == [
        dict(
            original,
            item_name="Ny mælk",
            matched_item_name="Ny mælk",
        )
    ]

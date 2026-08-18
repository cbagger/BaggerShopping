import json
import os
from types import SimpleNamespace

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")
os.environ.setdefault("SAMSUNG_LIST_ID", "test-list")

from fastapi.testclient import TestClient

import app.mobile_main as mobile
import app.mobile_offer_metadata as metadata_module


client = TestClient(mobile.app)
AUTH = {"Authorization": "Bearer test-token"}


def _selected_offer_payload(item_name="Hamburger Buns 6-pak", **overrides):
    payload = {
        "item_name": item_name,
        "retailer": "MENY",
        "price": 14.0,
        "valid_from": "14.08.2026",
        "valid_until": "20.08.2026",
        "offer_id": "buns-offer",
        "publication_id": "meny-current",
        "matched_item_name": item_name,
        "offer_snapshot": None,
    }
    payload.update(overrides)
    return payload


def test_name_bound_offer_metadata_promotes_once_to_samsung_item_id(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    payload = _selected_offer_payload()
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=payload).status_code == 200

    calls = 0

    async def fake_active_items():
        nonlocal calls
        calls += 1
        return [SimpleNamespace(id="item-123", name="Hamburger Buns 6-pak")]

    monkeypatch.setattr(
        metadata_module,
        "_active_items_for_one_time_binding",
        fake_active_items,
    )

    first = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert first.status_code == 200
    row = first.json()["metadata"][0]
    assert row["item_id"] == "item-123"
    assert row["item_name"] == "Hamburger Buns 6-pak"
    assert row["offer_id"] == "buns-offer"
    assert row["pinned"] is True
    assert calls == 1

    persisted = json.loads(store_path.read_text("utf-8"))
    assert list(persisted) == ["item:item-123"]

    async def must_not_read_again():
        raise AssertionError("ID-bound metadata must not trigger another list read")

    monkeypatch.setattr(
        metadata_module,
        "_active_items_for_one_time_binding",
        must_not_read_again,
    )
    second = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert second.status_code == 200
    assert second.json()["metadata"][0]["item_id"] == "item-123"


def test_build61_selected_offer_survives_samsung_dropping_terminal_pack_suffix(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    # Build 61 did not send an explicit pinned field. The concrete
    # offer_id/publication_id pair is therefore the backward-compatible proof
    # that this was a user-selected offer from Tilbud/Aviser.
    payload = _selected_offer_payload("Hamburger Buns 6-pak")
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=payload).status_code == 200

    async def normalized_samsung_item():
        return [SimpleNamespace(id="item-123", name="Hamburger Buns")]

    monkeypatch.setattr(
        metadata_module,
        "_active_items_for_one_time_binding",
        normalized_samsung_item,
    )

    response = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert response.status_code == 200
    row = response.json()["metadata"][0]

    assert row["item_id"] == "item-123"
    assert row["item_name"] == "Hamburger Buns"
    assert row["pinned"] is True
    assert row["retailer"] == "MENY"
    assert row["price"] == 14.0
    assert row["offer_id"] == "buns-offer"
    assert row["publication_id"] == "meny-current"

    persisted = json.loads(store_path.read_text("utf-8"))
    assert list(persisted) == ["item:item-123"]


def test_pinned_alias_does_not_drop_weight_or_volume_identity(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    payload = _selected_offer_payload(
        "Coca-Cola Zero 1,5 l",
        offer_id="cola-offer",
        matched_item_name="Coca-Cola Zero 1,5 l",
    )
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=payload).status_code == 200

    async def different_product_identity():
        return [SimpleNamespace(id="cola-id", name="Coca-Cola Zero")]

    monkeypatch.setattr(
        metadata_module,
        "_active_items_for_one_time_binding",
        different_product_identity,
    )

    response = client.get("/api/mobile/v1/offer-metadata", headers=AUTH)
    assert response.status_code == 200
    row = response.json()["metadata"][0]
    assert row["item_id"] is None
    assert row["item_name"] == "Coca-Cola Zero 1,5 l"


def test_unpinned_write_cannot_replace_existing_user_selected_offer(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    assert client.put(
        "/api/mobile/v1/offer-metadata",
        headers=AUTH,
        json=_selected_offer_payload("Hamburger Buns"),
    ).status_code == 200

    automatic = {
        "item_name": "Hamburger Buns",
        "retailer": "Netto",
        "price": 10.0,
        "matched_item_name": "Hamburger Buns",
        "pinned": False,
    }
    response = client.put(
        "/api/mobile/v1/offer-metadata",
        headers=AUTH,
        json=automatic,
    )
    assert response.status_code == 200
    assert response.json()["pinned_preserved"] is True

    stored = client.get("/api/mobile/v1/offer-metadata", headers=AUTH).json()["metadata"][0]
    assert stored["retailer"] == "MENY"
    assert stored["price"] == 14.0
    assert stored["offer_id"] == "buns-offer"
    assert stored["pinned"] is True


def test_new_explicit_selection_can_replace_pin_and_keeps_item_id(monkeypatch, tmp_path):
    store_path = tmp_path / "offer-metadata.json"
    monkeypatch.setenv("OFFER_METADATA_STORE_PATH", str(store_path))

    first = _selected_offer_payload("Hamburger Buns", item_id="item-123", pinned=True)
    assert client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=first).status_code == 200

    replacement = _selected_offer_payload(
        "Hamburger Buns",
        retailer="Netto",
        price=11.0,
        offer_id="netto-buns",
        publication_id="netto-current",
        pinned=True,
    )
    response = client.put("/api/mobile/v1/offer-metadata", headers=AUTH, json=replacement)
    assert response.status_code == 200
    assert response.json()["pinned"] is True
    assert response.json()["item_id"] == "item-123"

    stored = client.get("/api/mobile/v1/offer-metadata", headers=AUTH).json()["metadata"][0]
    assert stored["item_id"] == "item-123"
    assert stored["retailer"] == "Netto"
    assert stored["price"] == 11.0
    assert stored["offer_id"] == "netto-buns"
    assert stored["publication_id"] == "netto-current"


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
                "pinned": True,
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

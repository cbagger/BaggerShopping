import asyncio
import time

import app.samsung as samsung
from app.models import ShoppingItem
from app.samsung import SamsungFoodClient, reset_item_snapshot_cache_for_tests


def test_checked_write_uses_one_read_and_does_not_wait_for_eventual_consistency(monkeypatch):
    reset_item_snapshot_cache_for_tests()
    reads = []
    writes = []

    async def fake_find_item(self, item_id, **kwargs):
        reads.append(item_id)
        return ShoppingItem(id=item_id, name="Mælk", checked=False, quantity=2.0, unit="stk")

    async def fake_post_sync_items(self, body):
        writes.append(body)
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "_find_checked_write_item", fake_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"

    result = asyncio.run(client.set_item_checked("item-123", True))

    assert result == {
        "grpc_status": 0,
        "item_id": "item-123",
        "item_name": "Mælk",
        "rebound": False,
    }
    assert reads == ["item-123"]
    assert len(writes) == 1


def test_checked_write_uses_recent_server_snapshot_without_another_read(monkeypatch):
    reset_item_snapshot_cache_for_tests()
    writes = []

    async def unexpected_find_item(self, item_id, **kwargs):
        raise AssertionError(f"unexpected Samsung read for {item_id}")

    async def fake_post_sync_items(self, body):
        writes.append(body)
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "_find_checked_write_item", unexpected_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"
    client._remember_item_snapshot([
        ShoppingItem(
            id="item-123",
            name="Mælk",
            checked=False,
            quantity=2.0,
            unit="stk",
        )
    ])

    result = asyncio.run(client.set_item_checked("item-123", True))

    assert result == {
        "grpc_status": 0,
        "item_id": "item-123",
        "item_name": "Mælk",
        "rebound": False,
    }
    assert len(writes) == 1
    assert client._cached_item("item-123").checked is True


def test_expired_server_snapshot_falls_back_to_fresh_read(monkeypatch):
    reset_item_snapshot_cache_for_tests()
    reads = []

    async def fake_find_item(self, item_id, **kwargs):
        reads.append(item_id)
        return ShoppingItem(id=item_id, name="Mælk", checked=False)

    async def fake_post_sync_items(self, body):
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "_find_checked_write_item", fake_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"
    client._remember_item_snapshot([
        ShoppingItem(id="item-123", name="Gammelt navn", checked=False)
    ])
    _, items = samsung._item_snapshots[client.list_id]
    samsung._item_snapshots[client.list_id] = (time.monotonic() - 121.0, items)

    asyncio.run(client.set_item_checked("item-123", True))

    assert reads == ["item-123"]


def test_checked_write_rebinds_stale_id_to_unique_same_name_item(monkeypatch):
    reset_item_snapshot_cache_for_tests()
    writes = []

    async def fake_get_list(self):
        return samsung.ShoppingListResponse(
            list_id=self.list_id,
            name="Indkøbsliste",
            items=[ShoppingItem(id="new-item", name="coca cola", checked=False)],
        )

    async def fake_post_sync_items(self, body):
        writes.append(body)
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "get_list", fake_get_list)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"

    result = asyncio.run(client.set_item_checked(
        "stale-item",
        True,
        fallback_name="Coca   Cola",
    ))

    assert result == {
        "grpc_status": 0,
        "item_id": "new-item",
        "item_name": "coca cola",
        "rebound": True,
    }
    assert len(writes) == 1

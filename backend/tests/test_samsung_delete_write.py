import asyncio
from types import SimpleNamespace

from app.samsung import SamsungFoodClient, reset_item_snapshot_cache_for_tests


def test_delete_write_accepts_grpc_ack_without_eventual_consistency_read(monkeypatch):
    reset_item_snapshot_cache_for_tests()
    reads = []
    writes = []

    async def fake_find_item(self, item_id):
        reads.append(item_id)
        return SimpleNamespace(name="Mælk")

    async def fake_post_sync_items(self, body):
        writes.append(body)
        return {"grpc_status": 0}

    async def unexpected_get_list(self):
        raise AssertionError("delete must not verify against Samsung's stale read side")

    monkeypatch.setattr(SamsungFoodClient, "_find_item", fake_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    monkeypatch.setattr(SamsungFoodClient, "get_list", unexpected_get_list)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"

    result = asyncio.run(client.delete_item("item-123"))

    assert result == {"grpc_status": 0}
    assert reads == ["item-123"]
    assert len(writes) == 1

import asyncio
from types import SimpleNamespace

from app.samsung import SamsungFoodClient


def test_checked_write_uses_one_read_and_does_not_wait_for_eventual_consistency(monkeypatch):
    reads = []
    writes = []

    async def fake_find_item(self, item_id):
        reads.append(item_id)
        return SimpleNamespace(name="Mælk", quantity=2.0, unit="stk")

    async def fake_post_sync_items(self, body):
        writes.append(body)
        return {"grpc_status": 0}

    monkeypatch.setattr(SamsungFoodClient, "_find_item", fake_find_item)
    monkeypatch.setattr(SamsungFoodClient, "_post_sync_items", fake_post_sync_items)
    client = object.__new__(SamsungFoodClient)
    client.list_id = "family-list"

    result = asyncio.run(client.set_item_checked("item-123", True))

    assert result == {"grpc_status": 0}
    assert reads == ["item-123"]
    assert len(writes) == 1

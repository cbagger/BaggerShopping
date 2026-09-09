import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app import shopping_cleanup
from app.shopping_cleanup import delete_checked_local_households, seconds_until_next_midnight


def test_seconds_until_next_copenhagen_midnight():
    now = datetime(2026, 8, 13, 23, 59, 30, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert seconds_until_next_midnight(now) == 30


def test_seconds_until_midnight_handles_daylight_saving_timezone():
    now = datetime(2026, 1, 5, 12, 0, 0, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert seconds_until_next_midnight(now) == 12 * 60 * 60


def test_midnight_cleanup_isolated_local_households(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    from app.households import save_store

    save_store({"households": {
        "a": {"id": "a", "list_backend": "local", "items": [
            {"id": "a1", "name": "Købt A", "checked": True},
            {"id": "a2", "name": "Aktiv A", "checked": False},
        ]},
        "b": {"id": "b", "list_backend": "local", "items": [
            {"id": "b1", "name": "Købt B", "checked": True},
        ]},
    }})

    assert delete_checked_local_households() == (2, 2)
    from app.households import load_store
    store = load_store()
    assert [item["name"] for item in store["households"]["a"]["items"]] == ["Aktiv A"]
    assert store["households"]["b"]["items"] == []


def test_midnight_cleanup_uses_family_scoped_samsung_session(monkeypatch):
    selected_contexts = []
    deleted_ids = []
    saved_metadata = []

    class FamilyClient:
        async def get_list(self):
            return SimpleNamespace(items=[
                SimpleNamespace(id="bought-1", name="Mælk", checked=True),
                SimpleNamespace(id="active-1", name="Brød", checked=False),
            ])

        async def delete_item(self, item_id):
            deleted_ids.append(item_id)
            return {"grpc_status": 0}

    async def select_family_client(context):
        selected_contexts.append(context)
        return FamilyClient()

    monkeypatch.setattr(shopping_cleanup, "family_samsung_client", select_family_client)
    monkeypatch.setattr(shopping_cleanup, "delete_checked_local_households", lambda: (0, 0))
    monkeypatch.setattr(
        shopping_cleanup,
        "load_offer_metadata_store",
        lambda: {
            shopping_cleanup.offer_metadata_key("Mælk", "bought-1"): {"item_name": "Mælk"},
            shopping_cleanup.offer_metadata_key("Mælk"): {"item_name": "Mælk"},
        },
    )
    monkeypatch.setattr(
        shopping_cleanup,
        "save_offer_metadata_store",
        lambda metadata: saved_metadata.append(metadata),
    )

    result = asyncio.run(shopping_cleanup.delete_checked_items())

    assert selected_contexts[0].household_id == "family-bagger"
    assert deleted_ids == ["bought-1"]
    assert result == {"found": 1, "deleted": 1, "failed": 0}
    assert saved_metadata == [{}]


def test_midnight_cleanup_keeps_legacy_samsung_fallback(monkeypatch):
    deleted_ids = []

    class LegacyClient:
        async def get_list(self):
            return SimpleNamespace(items=[
                SimpleNamespace(id="bought-legacy", name="Smør", checked=True),
            ])

        async def delete_item(self, item_id):
            deleted_ids.append(item_id)
            return {"grpc_status": 0}

    async def no_family_client(_context):
        return None

    legacy_client = LegacyClient()
    monkeypatch.setattr(shopping_cleanup, "family_samsung_client", no_family_client)
    monkeypatch.setattr(shopping_cleanup, "SamsungFoodClient", lambda: legacy_client)
    monkeypatch.setattr(shopping_cleanup, "delete_checked_local_households", lambda: (0, 0))
    monkeypatch.setattr(shopping_cleanup, "load_offer_metadata_store", lambda: {})
    monkeypatch.setattr(shopping_cleanup, "save_offer_metadata_store", lambda _metadata: None)

    result = asyncio.run(shopping_cleanup.delete_checked_items())

    assert deleted_ids == ["bought-legacy"]
    assert result == {"found": 1, "deleted": 1, "failed": 0}


def test_legacy_worker_context_selects_samsung_family():
    from app.households import current_household, legacy_worker_context
    legacy_worker_context()
    assert current_household().household_id == "family-bagger"
    assert current_household().list_backend == "samsung"

from datetime import datetime
from zoneinfo import ZoneInfo

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

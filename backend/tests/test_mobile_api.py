import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient
import app.mobile_main as mobile
from app import households


client = TestClient(mobile.app)


def test_mobile_requires_token():
    response = client.get("/api/mobile/v1/list")
    assert response.status_code == 401


def test_mobile_rejects_wrong_token():
    response = client.get(
        "/api/mobile/v1/list",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_clear_checked_proxies_one_bulk_core_request(monkeypatch):
    calls = []

    async def core_delete(path: str):
        calls.append(path)

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"ok": True, "deleted_count": 3, "deleted_item_ids": ["1", "2", "3"]}

        return Response()

    monkeypatch.setattr(mobile, "core_delete", core_delete)
    response = client.delete(
        "/api/mobile/v1/actions/clear-checked",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 3
    assert calls == ["/api/shopping/actions/clear-checked"]


def test_legacy_samsung_status_is_migrated_to_family_record(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))

    async def core_get(path: str):
        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                if path == "/api/shopping":
                    return {"name": "Familiens liste", "list_id": "samsung-list-1", "items": []}
                return {"status": "ok", "samsung_auth": "valid"}
        return Response()

    monkeypatch.setattr(mobile, "core_get", core_get)
    response = client.get(
        "/api/mobile/v1/integrations/samsung-food",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "connected"
    assert response.json()["list_name"] == "Familiens liste"

    household = households.load_store()["households"]["family-bagger"]
    integration = household["integrations"]["samsung_food"]
    assert integration["storage_scope"] == "family-bagger"
    assert integration["migrated_from_legacy"] is True


def test_disconnect_copies_samsung_items_before_switching_to_local(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))

    async def core_get(_: str):
        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"name": "Køleskabet", "items": [{"id": "s1", "name": "Mælk", "checked": False}]}
        return Response()

    monkeypatch.setattr(mobile, "core_get", core_get)
    response = client.post(
        "/api/mobile/v1/integrations/samsung-food/disconnect",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["preserved_item_count"] == 1

    household = households.load_store()["households"]["family-bagger"]
    assert household["list_backend"] == "local"
    assert household["items"][0]["name"] == "Mælk"
    assert household["integrations"]["samsung_food"]["status"] == "not_connected"

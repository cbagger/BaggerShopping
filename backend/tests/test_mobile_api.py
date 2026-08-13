import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient
import app.mobile_main as mobile


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

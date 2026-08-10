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

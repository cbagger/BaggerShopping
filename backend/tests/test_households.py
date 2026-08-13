import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import households
from app.mobile_main import app


client = TestClient(app)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_two_households_have_isolated_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))

    first = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie A", "member_name": "Anna"})
    second = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie B", "member_name": "Bo"})
    assert first.status_code == second.status_code == 200
    token_a = first.json()["access_token"]
    token_b = second.json()["access_token"]

    assert client.post("/api/mobile/v1/items", headers=auth(token_a), json={"name": "Mælk"}).status_code == 200
    assert client.post("/api/mobile/v1/items", headers=auth(token_b), json={"name": "Smør"}).status_code == 200

    assert [item["name"] for item in client.get("/api/mobile/v1/list", headers=auth(token_a)).json()["items"]] == ["Mælk"]
    assert [item["name"] for item in client.get("/api/mobile/v1/list", headers=auth(token_b)).json()["items"]] == ["Smør"]


def test_invite_joins_same_household_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    created = client.post("/api/mobile/v1/households/create", json={"household_name": "Familien", "member_name": "Ejer"}).json()
    invite = client.post("/api/mobile/v1/households/invite", headers=auth(created["access_token"]), json={"expires_in_days": 7})
    assert invite.status_code == 200

    joined = client.post("/api/mobile/v1/households/join", json={"invite_code": invite.json()["invite_code"], "member_name": "Partner"})
    assert joined.status_code == 200
    assert joined.json()["household_id"] == created["household_id"]
    assert client.post("/api/mobile/v1/households/join", json={"invite_code": invite.json()["invite_code"], "member_name": "Fremmed"}).status_code == 404


def test_legacy_token_maps_to_existing_samsung_household(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    response = client.get("/api/mobile/v1/households/me", headers=auth("test-token"))
    assert response.status_code == 200
    assert response.json()["household_id"] == households.LEGACY_HOUSEHOLD_ID
    assert response.json()["list_backend"] == "samsung"

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


def test_owner_can_list_rename_and_revoke_member(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    owner = client.post("/api/mobile/v1/households/create", json={"household_name": "Familien", "member_name": "Ejer"}).json()
    invite = client.post("/api/mobile/v1/households/invite", headers=auth(owner["access_token"]), json={"expires_in_days": 7}).json()
    joined = client.post("/api/mobile/v1/households/join", json={"invite_code": invite["invite_code"], "member_name": "Partner"}).json()

    listed = client.get("/api/mobile/v1/households/members", headers=auth(owner["access_token"]))
    assert listed.status_code == 200
    partner = next(member for member in listed.json()["members"] if member["role"] == "member")
    assert client.patch(f"/api/mobile/v1/households/members/{partner['id']}", headers=auth(owner["access_token"]), json={"name": "Nyt navn"}).status_code == 200
    assert client.delete(f"/api/mobile/v1/households/members/{partner['id']}", headers=auth(owner["access_token"])).status_code == 200
    assert client.get("/api/mobile/v1/households/me", headers=auth(joined["access_token"])).status_code == 401


def test_member_cannot_administer_and_owner_cannot_be_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    owner = client.post("/api/mobile/v1/households/create", json={"household_name": "Familien", "member_name": "Ejer"}).json()
    invite = client.post("/api/mobile/v1/households/invite", headers=auth(owner["access_token"]), json={"expires_in_days": 7}).json()
    member = client.post("/api/mobile/v1/households/join", json={"invite_code": invite["invite_code"], "member_name": "Medlem"}).json()

    assert client.get("/api/mobile/v1/households/members", headers=auth(member["access_token"])).status_code == 403
    listed = client.get("/api/mobile/v1/households/members", headers=auth(owner["access_token"])).json()["members"]
    owner_record = next(record for record in listed if record["role"] == "owner")
    assert client.delete(f"/api/mobile/v1/households/members/{owner_record['id']}", headers=auth(owner["access_token"])).status_code == 409


def test_existing_member_without_id_is_migrated_without_changing_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    member_token = "old-marielle-token"
    households.save_store({"households": {
        households.LEGACY_HOUSEHOLD_ID: {
            "id": households.LEGACY_HOUSEHOLD_ID,
            "name": "Familien Bagger",
            "list_backend": "samsung",
            "members": {households._hash(member_token): {"name": "Marielle", "role": "member"}},
            "items": [],
        }
    }})

    listed = client.get("/api/mobile/v1/households/members", headers=auth("test-token"))
    assert listed.status_code == 200
    marielle = next(member for member in listed.json()["members"] if member["name"] == "Marielle")
    assert marielle["id"]
    assert client.get("/api/mobile/v1/households/me", headers=auth(member_token)).status_code == 200

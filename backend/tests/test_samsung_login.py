import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import households
from app.mobile_main import app


client = TestClient(app)
AUTH = {"Authorization": "Bearer test-token"}


def test_family_login_session_is_one_time_and_activates_selected_list(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    monkeypatch.setenv("SAMSUNG_LOGIN_SESSION_STORE_PATH", str(tmp_path / "login-sessions.json"))
    monkeypatch.setenv("SAMSUNG_LOGIN_PUBLIC_BASE_URL", "https://login.example.test")
    monkeypatch.setenv("SAMSUNG_LOGIN_BROKER_KEY", "broker-secret")

    started = client.post("/api/mobile/v1/integrations/samsung-food/login/start", headers=AUTH)
    assert started.status_code == 200
    payload = started.json()
    login_token = payload["login_url"].rsplit("/", 1)[-1]

    claimed = client.get(f"/api/mobile/v1/integrations/samsung-food/broker/session/{login_token}")
    assert claimed.status_code == 200
    assert claimed.json()["household_id"] == "family-bagger"
    assert "/family-bagger/" in claimed.json()["browser_profile"]
    assert client.get(f"/api/mobile/v1/integrations/samsung-food/broker/session/{login_token}").status_code == 404

    denied = client.post(
        f"/api/mobile/v1/integrations/samsung-food/broker/session/{payload['session_id']}/complete",
        json={"lists": [{"id": "list-1", "name": "Køleskabet"}]},
    )
    assert denied.status_code == 401

    completed = client.post(
        f"/api/mobile/v1/integrations/samsung-food/broker/session/{payload['session_id']}/complete",
        headers={"X-Kurv-Broker-Key": "broker-secret"},
        json={"lists": [{"id": "list-1", "name": "Køleskabet"}, {"id": "list-2", "name": "Sommerhus"}]},
    )
    assert completed.status_code == 200

    status = client.get(f"/api/mobile/v1/integrations/samsung-food/login/{payload['session_id']}", headers=AUTH)
    assert status.json()["status"] == "choose_list"
    assert len(status.json()["lists"]) == 2

    selected = client.post(
        "/api/mobile/v1/integrations/samsung-food/login/select-list",
        headers=AUTH,
        json={"session_id": payload["session_id"], "list_id": "list-2"},
    )
    assert selected.status_code == 200
    integration = households.load_store()["households"]["family-bagger"]["integrations"]["samsung_food"]
    assert integration["list_id"] == "list-2"
    assert integration["storage_scope"] == "family-bagger"
    assert integration["auth_state_path"].endswith("/family-bagger/samsung-food/auth-state.json")

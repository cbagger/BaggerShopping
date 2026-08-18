import os
import stat
import subprocess
from pathlib import Path

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app.mobile_main import app
from app.samsung_broker_auth import broker_key


client = TestClient(app)
AUTH = {"Authorization": "Bearer test-token"}


def test_explicit_broker_key_wins_without_touching_persistent_file(monkeypatch, tmp_path):
    path = tmp_path / "broker.key"
    configured = "x" * 48
    monkeypatch.setenv("SAMSUNG_LOGIN_BROKER_KEY", configured)
    monkeypatch.setenv("SAMSUNG_LOGIN_BROKER_KEY_PATH", str(path))

    assert broker_key() == configured
    assert not path.exists()


def test_broker_key_is_generated_once_and_persisted_with_private_mode(monkeypatch, tmp_path):
    path = tmp_path / "broker.key"
    monkeypatch.delenv("SAMSUNG_LOGIN_BROKER_KEY", raising=False)
    monkeypatch.setenv("SAMSUNG_LOGIN_BROKER_KEY_PATH", str(path))

    first = broker_key()
    second = broker_key()

    assert first == second
    assert len(first) >= 32
    assert path.read_text("utf-8").strip() == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_mobile_broker_complete_uses_persistent_key_when_env_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    monkeypatch.setenv("SAMSUNG_LOGIN_SESSION_STORE_PATH", str(tmp_path / "login-sessions.json"))
    monkeypatch.setenv("SAMSUNG_LOGIN_PUBLIC_BASE_URL", "https://login.example.test")
    monkeypatch.setenv("SAMSUNG_LOGIN_BROKER_KEY_PATH", str(tmp_path / "broker.key"))
    monkeypatch.delenv("SAMSUNG_LOGIN_BROKER_KEY", raising=False)

    started = client.post("/api/mobile/v1/integrations/samsung-food/login/start", headers=AUTH)
    assert started.status_code == 200
    payload = started.json()
    login_token = payload["login_url"].rsplit("/", 1)[-1]

    claimed = client.get(f"/api/mobile/v1/integrations/samsung-food/broker/session/{login_token}")
    assert claimed.status_code == 200

    completed = client.post(
        f"/api/mobile/v1/integrations/samsung-food/broker/session/{payload['session_id']}/complete",
        headers={"X-Kurv-Broker-Key": broker_key()},
        json={"lists": [{"id": "list-1", "name": "Køleskabet"}]},
    )
    assert completed.status_code == 200


def test_login_broker_start_script_is_shell_valid_and_cleans_stale_xvfb_state():
    script = Path(__file__).resolve().parents[1] / "start-login-broker.sh"
    subprocess.run(["sh", "-n", str(script)], check=True)
    text = script.read_text("utf-8")

    assert "from app.samsung_broker_auth import broker_key" in text
    assert "rm -f /tmp/.X99-lock /tmp/.X11-unix/X99" in text
    assert "[ -S /tmp/.X11-unix/X99 ]" in text

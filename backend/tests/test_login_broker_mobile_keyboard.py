import asyncio
import time
from pathlib import Path

from starlette.requests import Request

from app import login_broker
from app.login_broker import ActiveLogin, _cookie_secure_for_public_url, _hash, _page


def test_private_lan_http_allows_session_cookie_without_secure_flag():
    assert _cookie_secure_for_public_url("http://192.168.0.111:8091") is False
    assert _cookie_secure_for_public_url("http://10.0.0.5:8091") is False
    assert _cookie_secure_for_public_url("http://127.0.0.1:8091") is False


def test_public_or_https_broker_keeps_secure_cookie():
    assert _cookie_secure_for_public_url("https://shopping-login.example.test") is True
    assert _cookie_secure_for_public_url("http://shopping-login.example.test") is True
    assert _cookie_secure_for_public_url("") is True


def test_mobile_login_page_has_ios_keyboard_bridge():
    page = _page("session-123")

    assert 'id="keyboardToggle"' in page
    assert 'id="keyboardInput"' in page
    assert 'id="sendText"' in page
    assert "rfb.sendKey(keysymForCharacter(char),null)" in page
    assert "rfb.sendKey(0xff08,'Backspace')" in page
    assert "rfb.sendKey(0xff09,'Tab')" in page
    assert "rfb.sendKey(0xff0d,'Enter')" in page
    assert "keyboardInput.focus()" in page


def test_same_browser_can_reopen_active_login_instead_of_getting_family_conflict():
    class Context:
        async def close(self):
            raise AssertionError("A reopened session must keep its browser context")

    active = ActiveLogin(
        session_id="session-123",
        household_id="family-bagger",
        secret="browser-secret",
        login_token_hash=_hash("same-login-token"),
        expires_at=int(time.time()) + 600,
        auth_state=Path("/tmp/auth.json"),
        context=Context(),
        discovered_lists={},
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/session/same-login-token",
        "headers": [(b"cookie", b"kurv_samsung_login=browser-secret")],
    })
    previous = login_broker.ACTIVE
    login_broker.ACTIVE = active
    try:
        response = asyncio.run(login_broker.open_session("same-login-token", request))
    finally:
        login_broker.ACTIVE = previous

    assert response.status_code == 200
    assert "Kurv · Samsung Food-login" in response.body.decode()


def test_discard_unreadable_existing_list_before_asking_family_to_choose(monkeypatch):
    import httpx

    async def choices(_):
        return [{"id": "old-list", "name": "Indkøbsliste"}, {"id": "new-list", "name": "Indkøbsliste"}]

    async def read(list_id, token):
        assert token == "browser-session"
        return httpx.Response(400 if list_id == "old-list" else 200)

    monkeypatch.setattr(login_broker, "_list_choices", choices)
    monkeypatch.setattr(login_broker.SamsungFoodClient, "read_list_response", read)

    assert asyncio.run(login_broker._readable_lists(None, "browser-session")) == [
        {"id": "new-list", "name": "Indkøbsliste"},
    ]


def test_expired_interactive_session_no_longer_blocks_maintenance(monkeypatch):
    from types import SimpleNamespace

    import pytest

    calls = []

    async def close():
        calls.append("close")

    async def refresh(target):
        calls.append(target)
        return "current"

    sleep_calls = 0

    async def sleep(_):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(login_broker, "ACTIVE", SimpleNamespace(
        expires_at=time.time() - 1,
        context=SimpleNamespace(close=close),
    ))
    monkeypatch.setattr(login_broker, "family_auth_targets", lambda: ["family"])
    monkeypatch.setattr(login_broker, "refresh_family_auth", refresh)
    monkeypatch.setattr(login_broker.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(login_broker._auth_refresh_loop())

    assert login_broker.ACTIVE is None
    assert calls == ["close", "family"]


def test_mobile_renewal_checks_key_and_coalesces_requests_for_the_same_token(monkeypatch, tmp_path):
    from app.auth import AuthState, SamsungAuthManager
    from app.samsung_auth_refresh import FamilyAuthTarget
    from fastapi.testclient import TestClient

    target = FamilyAuthTarget("family-bagger", tmp_path / "auth.json", tmp_path / "browser")
    manager = SamsungAuthManager(state_file=target.auth_state)
    manager.save_state(AuthState("expired", 100, "test"))
    calls = []

    async def refresh(value, *, force):
        assert force
        calls.append(value)
        manager.save_state(AuthState("fresh", 101, "browser-session-maintenance"))
        return "refreshed"

    monkeypatch.setattr(login_broker, "ACTIVE", None)
    monkeypatch.setattr(login_broker, "RECENT_REFRESH", {})
    monkeypatch.setattr(login_broker, "broker_key", lambda: "unit-test-key")
    monkeypatch.setattr(login_broker, "family_auth_targets", lambda: [target])
    monkeypatch.setattr(login_broker, "refresh_family_auth", refresh)

    client = TestClient(login_broker.app)
    url = "/internal/samsung-auth/refresh"
    payload = {"household_id": "family-bagger", "observed_at": 100}
    assert client.post(url, json=payload).status_code == 401
    assert calls == []
    headers = {"X-Kurv-Broker-Key": "unit-test-key"}
    assert client.post(url, json=payload, headers=headers).json() == {"status": "refreshed"}
    assert client.post(url, json=payload, headers=headers).json() == {"status": "refreshed"}
    assert calls == [target]

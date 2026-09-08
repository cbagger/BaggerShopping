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

from app.login_broker import _cookie_secure_for_public_url, _page


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

import asyncio
import json
import time
from pathlib import Path

from app import samsung_auth_refresh
from app.auth import AuthState
from app.samsung_auth_refresh import FamilyAuthTarget


def test_family_auth_targets_only_include_scoped_samsung_integrations(monkeypatch, tmp_path):
    store = tmp_path / "households.json"
    store.write_text(json.dumps({
        "households": {
            "family-bagger": {
                "list_backend": "samsung",
                "integrations": {"samsung_food": {
                    "auth_state_path": "/data/bagger-auth.json",
                    "browser_profile_path": "/data/bagger-browser",
                }},
            },
            "local-family": {
                "list_backend": "local",
                "integrations": {"samsung_food": {
                    "auth_state_path": "/data/local-auth.json",
                    "browser_profile_path": "/data/local-browser",
                }},
            },
        }
    }), "utf-8")
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(store))

    assert samsung_auth_refresh.family_auth_targets() == [FamilyAuthTarget(
        household_id="family-bagger",
        auth_state=Path("/data/bagger-auth.json"),
        browser_profile=Path("/data/bagger-browser"),
    )]


def test_expired_token_is_recovered_from_persistent_browser(monkeypatch, tmp_path):
    saved = []

    class Manager:
        def __init__(self, **_):
            pass

        def load_state(self):
            return AuthState(token="expired", updated_at=time.time() - 100, source="test")

        async def token_valid(self, token):
            return token == "fresh"

        async def _token_from_persistent_browser(self):
            return "fresh"

        def save_state(self, state):
            saved.append(state)

    monkeypatch.setattr(samsung_auth_refresh, "SamsungAuthManager", Manager)
    target = FamilyAuthTarget("family-bagger", tmp_path / "auth.json", tmp_path / "profile")

    assert asyncio.run(samsung_auth_refresh.refresh_family_auth(target)) == "refreshed"
    assert saved[0].token == "fresh"
    assert saved[0].source == "browser-session-maintenance"


def test_aging_unchanged_token_stays_due_for_next_refresh(monkeypatch, tmp_path):
    saved = []

    class Manager:
        def __init__(self, **_):
            pass

        def load_state(self):
            return AuthState(
                token="same-token",
                updated_at=time.time() - samsung_auth_refresh.REFRESH_AFTER_SECONDS - 1,
                source="test",
            )

        async def token_valid(self, token):
            return token == "same-token"

        async def _token_from_persistent_browser(self):
            return "same-token"

        def save_state(self, state):
            saved.append(state)

    monkeypatch.setattr(samsung_auth_refresh, "SamsungAuthManager", Manager)
    target = FamilyAuthTarget("family-bagger", tmp_path / "auth.json", tmp_path / "profile")

    assert asyncio.run(samsung_auth_refresh.refresh_family_auth(target)) == "unchanged"
    assert saved == []

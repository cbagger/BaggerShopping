import asyncio
import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")
os.environ.setdefault("SAMSUNG_LIST_ID", "test-list")

import pytest

from app.auth import AuthInteractionRequired
from app.households import HouseholdContext
from app import mobile_main, samsung_request_policy


def legacy_context() -> HouseholdContext:
    return HouseholdContext(
        household_id="family-bagger",
        household_name="Familien Bagger",
        member_name="Christoffer",
        role="owner",
        list_backend="samsung",
    )


def nonlegacy_context() -> HouseholdContext:
    return HouseholdContext(
        household_id="family-other",
        household_name="Anden familie",
        member_name="Owner",
        role="owner",
        list_backend="samsung",
    )


def test_mobile_main_uses_first_class_samsung_request_policy():
    assert mobile_main.family_samsung_client is samsung_request_policy.family_samsung_client


def test_legacy_family_without_complete_isolated_binding_falls_back_to_core(monkeypatch):
    async def fake_read_household(_):
        return {
            "integrations": {
                "samsung_food": {
                    "list_id": "legacy-list-only",
                }
            }
        }

    monkeypatch.setattr(samsung_request_policy, "read_household", fake_read_household)

    assert asyncio.run(samsung_request_policy.family_samsung_client(legacy_context())) is None


def test_nonlegacy_family_uses_request_only_auth(monkeypatch, tmp_path):
    auth_state = tmp_path / "auth-state.json"
    browser_profile = tmp_path / "chromium-profile"

    async def fake_read_household(_):
        return {
            "integrations": {
                "samsung_food": {
                    "list_id": "list-123",
                    "auth_state_path": str(auth_state),
                    "browser_profile_path": str(browser_profile),
                }
            }
        }

    monkeypatch.setattr(samsung_request_policy, "read_household", fake_read_household)

    client = asyncio.run(samsung_request_policy.family_samsung_client(nonlegacy_context()))

    assert client is not None
    assert client.list_id == "list-123"
    assert isinstance(client.auth, samsung_request_policy.RequestOnlySamsungAuthManager)


def test_request_only_auth_never_launches_browser(monkeypatch, tmp_path):
    auth = samsung_request_policy.RequestOnlySamsungAuthManager(
        state_file=tmp_path / "auth-state.json",
        browser_user_data_dir=tmp_path / "profile",
        allow_credential_fallback=False,
    )

    async def invalid_token(_):
        return False

    async def forbidden_browser():
        raise AssertionError("browser recovery must never run in mobile request path")

    monkeypatch.setattr(auth, "token_valid", invalid_token)
    monkeypatch.setattr(auth, "_token_from_persistent_browser", forbidden_browser)

    with pytest.raises(AuthInteractionRequired):
        asyncio.run(auth.get_token())

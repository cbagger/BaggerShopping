import asyncio
import json

from app.households import HouseholdContext, LEGACY_HOUSEHOLD_ID
from app.samsung import SamsungFoodClient
from app.samsung_request_policy import RequestOnlySamsungAuthManager, family_samsung_client


def _legacy_context() -> HouseholdContext:
    return HouseholdContext(
        household_id=LEGACY_HOUSEHOLD_ID,
        household_name="Familien Bagger",
        member_name="Christoffer",
        role="owner",
        list_backend="samsung",
    )


def test_legacy_family_prefers_completed_family_scoped_samsung_binding(monkeypatch, tmp_path):
    store_path = tmp_path / "households.json"
    auth_state = tmp_path / "integrations" / LEGACY_HOUSEHOLD_ID / "samsung-food" / "auth-state.json"
    browser_profile = tmp_path / "integrations" / LEGACY_HOUSEHOLD_ID / "samsung-food" / "chromium-profile"
    auth_state.parent.mkdir(parents=True)
    auth_state.write_text(
        '{"token":"token","updated_at":1,"source":"interactive-broker"}',
        encoding="utf-8",
    )
    browser_profile.mkdir(parents=True)
    store_path.write_text(
        json.dumps({
            "households": {
                LEGACY_HOUSEHOLD_ID: {
                    "id": LEGACY_HOUSEHOLD_ID,
                    "name": "Familien Bagger",
                    "list_backend": "samsung",
                    "members": {},
                    "items": [],
                    "offer_metadata": {},
                    "integrations": {
                        "samsung_food": {
                            "status": "connected",
                            "list_id": "fresh-family-list",
                            "list_name": "Indkøbsliste",
                            "auth_state_path": str(auth_state),
                            "browser_profile_path": str(browser_profile),
                        }
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(store_path))

    client = asyncio.run(family_samsung_client(_legacy_context()))

    assert isinstance(client, SamsungFoodClient)
    assert client.list_id == "fresh-family-list"
    assert isinstance(client.auth, RequestOnlySamsungAuthManager)
    assert client.auth.state_file == auth_state
    assert client.auth.browser_user_data_dir == browser_profile


def test_legacy_family_without_completed_binding_keeps_core_fallback(monkeypatch, tmp_path):
    store_path = tmp_path / "households.json"
    store_path.write_text(
        json.dumps({
            "households": {
                LEGACY_HOUSEHOLD_ID: {
                    "id": LEGACY_HOUSEHOLD_ID,
                    "name": "Familien Bagger",
                    "list_backend": "samsung",
                    "members": {},
                    "items": [],
                    "offer_metadata": {},
                    "integrations": {
                        "samsung_food": {
                            "status": "connected",
                            "list_id": "legacy-list-only",
                        }
                    },
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(store_path))

    client = asyncio.run(family_samsung_client(_legacy_context()))

    assert client is None

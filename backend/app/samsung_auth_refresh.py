from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .auth import AuthState, SamsungAuthManager


REFRESH_AFTER_SECONDS = max(
    3600,
    int(os.getenv("SAMSUNG_AUTH_REFRESH_AFTER_SECONDS", str(3 * 24 * 60 * 60))),
)


@dataclass(frozen=True)
class FamilyAuthTarget:
    household_id: str
    auth_state: Path
    browser_profile: Path


def family_auth_targets() -> list[FamilyAuthTarget]:
    path = Path(os.getenv("HOUSEHOLD_STORE_PATH", "/data/households.json"))
    try:
        store = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(store, dict) or not isinstance(store.get("households"), dict):
        return []

    targets: list[FamilyAuthTarget] = []
    for household_id, household in store.get("households", {}).items():
        if not isinstance(household, dict) or household.get("list_backend") != "samsung":
            continue
        integration = household.get("integrations", {}).get("samsung_food", {})
        auth_state = integration.get("auth_state_path")
        browser_profile = integration.get("browser_profile_path")
        if all(
            isinstance(value, str) and value
            for value in (household_id, auth_state, browser_profile)
        ):
            targets.append(FamilyAuthTarget(
                household_id=household_id,
                auth_state=Path(auth_state),
                browser_profile=Path(browser_profile),
            ))
    return targets


async def refresh_family_auth(target: FamilyAuthTarget) -> str:
    """Refresh an aging family token from its persistent Samsung browser."""

    manager = SamsungAuthManager(
        state_file=target.auth_state,
        browser_user_data_dir=target.browser_profile,
        allow_credential_fallback=False,
    )
    state = manager.load_state()
    current_valid = await manager.token_valid(state.token)
    age = time.time() - (state.updated_at or 0)
    if current_valid and age < REFRESH_AFTER_SECONDS:
        return "current"

    token = await manager._token_from_persistent_browser()
    if not token or not await manager.token_valid(token):
        return "interaction_required"

    # Do not move updated_at forward when Samsung returned the exact same aging
    # token. The next pass will keep trying until Samsung actually rotates it.
    if current_valid and token == state.token:
        return "unchanged"

    manager.save_state(AuthState(
        token=token,
        updated_at=time.time(),
        source="browser-session-maintenance",
    ))
    return "refreshed"

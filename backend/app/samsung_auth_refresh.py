from __future__ import annotations

import json
import os
import time
from tempfile import NamedTemporaryFile
from dataclasses import dataclass
from pathlib import Path

from .auth import AuthInteractionRequired, AuthState, SamsungAuthManager
from .config import settings
from .households import LEGACY_HOUSEHOLD_ID
from .samsung import SamsungFoodClient


REFRESH_AFTER_SECONDS = max(
    3600,
    int(os.getenv("SAMSUNG_AUTH_REFRESH_AFTER_SECONDS", str(3 * 24 * 60 * 60))),
)


@dataclass(frozen=True)
class FamilyAuthTarget:
    household_id: str
    auth_state: Path
    browser_profile: Path
    list_id: str | None = None


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
                list_id=integration.get("list_id"),
            ))
    return targets


async def _token_from_account_login(target: FamilyAuthTarget) -> str | None:
    # The existing QNAP account belongs to the legacy family. Other families
    # use only their own Samsung profiles.
    if (target.household_id != LEGACY_HOUSEHOLD_ID or not target.list_id
            or not settings.samsung_account_email or not settings.samsung_account_password):
        return None

    profile = target.browser_profile.with_name("automatic-account-profile")
    manager = SamsungAuthManager(state_file=target.auth_state, browser_user_data_dir=profile)
    attempt = target.auth_state.with_name("automatic-account-attempt")
    try:
        if time.time() - attempt.stat().st_mtime < 6 * 3600:
            return None
    except FileNotFoundError:
        pass

    # Store only the attempt time so a restart cannot retry a rejected password
    # in a loop. The regular family profile is untouched during this attempt.
    with NamedTemporaryFile(dir=attempt.parent, delete=False) as marker:
        Path(marker.name).replace(attempt)
    try:
        token = await manager._login_with_credentials()
    except AuthInteractionRequired:
        return None
    if not token or not await manager.token_valid(token):
        return None
    return token if await _can_read_bound_list(target, token) else None


async def _can_read_bound_list(target: FamilyAuthTarget, token: str) -> bool:
    if not target.list_id:
        return False
    response = await SamsungFoodClient.read_list_response(target.list_id, token)
    if response.status_code != 200:
        return False
    try:
        return response.json()["list"]["id"] == target.list_id
    except (KeyError, TypeError, ValueError):
        return False


async def refresh_family_auth(target: FamilyAuthTarget, *, force: bool = False) -> str:
    """Refresh an aging family token from its persistent Samsung browser."""

    manager = SamsungAuthManager(
        state_file=target.auth_state,
        browser_user_data_dir=target.browser_profile,
        allow_credential_fallback=False,
    )
    state = manager.load_state()
    current_valid = await manager.token_valid(state.token)
    age = time.time() - (state.updated_at or 0)
    if current_valid and not force and age < REFRESH_AFTER_SECONDS:
        return "current"

    using_account_profile = state.source == "automatic-account-login"
    source = "automatic-account-login" if using_account_profile else "browser-session-maintenance"
    if using_account_profile:
        manager.browser_user_data_dir = target.browser_profile.with_name("automatic-account-profile")
    token = await manager._token_from_persistent_browser()
    valid = bool(token) and await manager.token_valid(token)
    if valid and using_account_profile:
        valid = await _can_read_bound_list(target, token)
    if not valid:
        if current_valid:
            return "unchanged"
        token = await _token_from_account_login(target)
        if not token:
            return "interaction_required"
        source = "automatic-account-login"

    # Do not move updated_at forward when Samsung returned the exact same aging
    # token. The next pass will keep trying until Samsung actually rotates it.
    if current_valid and token == state.token:
        return "unchanged"

    manager.save_state(AuthState(
        token=token,
        updated_at=time.time(),
        source=source,
    ))
    return "refreshed"

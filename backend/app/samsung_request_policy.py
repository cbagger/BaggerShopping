from __future__ import annotations

from pathlib import Path
from typing import Any

from .auth import AuthInteractionRequired, SamsungAuthManager
from .households import LEGACY_HOUSEHOLD_ID, HouseholdContext, read_household
from .samsung import SamsungFoodClient


class RequestOnlySamsungAuthManager(SamsungAuthManager):
    """Samsung auth policy for ordinary iPhone/API requests.

    Interactive browser recovery belongs to the isolated login broker. A normal
    request may only reuse an already persisted token; if that token is no
    longer valid the API reports that reconnect is required instead of opening
    Playwright from the request path.
    """

    async def get_token(self, force_refresh: bool = False) -> str:
        state = self.load_state()
        if not force_refresh and await self.token_valid(state.token):
            return state.token  # type: ignore[return-value]
        raise AuthInteractionRequired(
            "Samsung Food skal forbindes igen via Kurvs integrationsflow."
        )


async def family_samsung_client(context: HouseholdContext) -> Any | None:
    """Return the request-safe Samsung client for one household.

    Familien Bagger intentionally stays on the proven core connector. Other
    families may use their isolated persisted Samsung token, but never browser
    recovery during an ordinary mobile request.
    """

    if context.household_id == LEGACY_HOUSEHOLD_ID:
        return None

    household = await read_household(context)
    integration = household.get("integrations", {}).get("samsung_food", {})
    list_id = integration.get("list_id")
    auth_state = integration.get("auth_state_path")
    browser_profile = integration.get("browser_profile_path")
    if not all(
        isinstance(value, str) and value
        for value in (list_id, auth_state, browser_profile)
    ):
        return None

    auth = RequestOnlySamsungAuthManager(
        state_file=Path(auth_state),
        browser_user_data_dir=Path(browser_profile),
        allow_credential_fallback=False,
    )
    return SamsungFoodClient(list_id=list_id, auth=auth)


__all__ = ["RequestOnlySamsungAuthManager", "family_samsung_client"]

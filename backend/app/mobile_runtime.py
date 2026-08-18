from __future__ import annotations

from pathlib import Path
from typing import Any

from . import mobile_main as mobile
from .auth import AuthInteractionRequired, SamsungAuthManager
from .households import LEGACY_HOUSEHOLD_ID, HouseholdContext, read_household
from .samsung import SamsungFoodClient


class RequestOnlySamsungAuthManager(SamsungAuthManager):
    """Samsung auth for normal mobile requests.

    A request from the iPhone must never launch Playwright or an interactive
    browser recovery flow. The isolated login broker owns browser login. Normal
    API traffic may only use a token that the broker has already persisted.
    """

    async def get_token(self, force_refresh: bool = False) -> str:
        state = self.load_state()
        if not force_refresh and await self.token_valid(state.token):
            return state.token  # type: ignore[return-value]
        raise AuthInteractionRequired(
            "Samsung Food skal forbindes igen via Kurvs integrationsflow."
        )


async def safe_family_samsung_client(context: HouseholdContext) -> Any | None:
    """Return a request-safe family client without disturbing legacy Bagger.

    Familien Bagger is intentionally still backed by the proven core Samsung
    connector on the QNAP. It must not be diverted into an isolated browser
    profile merely because integration metadata exists. Other families use
    their isolated persisted token, but browser recovery is never attempted in
    an ordinary request path.
    """
    if context.household_id == LEGACY_HOUSEHOLD_ID:
        return None

    household = await read_household(context)
    integration = household.get("integrations", {}).get("samsung_food", {})
    list_id = integration.get("list_id")
    auth_state = integration.get("auth_state_path")
    browser_profile = integration.get("browser_profile_path")
    if not all(isinstance(value, str) and value for value in (list_id, auth_state, browser_profile)):
        return None

    auth = RequestOnlySamsungAuthManager(
        state_file=Path(auth_state),
        browser_user_data_dir=Path(browser_profile),
        allow_credential_fallback=False,
    )
    return SamsungFoodClient(list_id=list_id, auth=auth)


# Samsung request policy is the only compatibility assignment left in this
# runtime module. Offer reader/serialization and Luna pricing now use their
# explicit first-class code paths directly.
mobile.family_samsung_client = safe_family_samsung_client
app = mobile.app

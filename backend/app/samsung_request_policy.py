from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from .auth import AuthError, AuthInteractionRequired, SamsungAuthManager
from .households import LEGACY_HOUSEHOLD_ID, HouseholdContext, read_household
from .samsung import SamsungFoodClient
from .samsung_broker_auth import broker_key


class RequestOnlySamsungAuthManager(SamsungAuthManager):
    """Reuse the family token and ask the isolated broker to renew it on rejection."""

    def __init__(self, *, household_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.household_id = household_id

    async def get_token(self, force_refresh: bool = False) -> str:
        state = self.load_state()
        if not force_refresh and await self.token_valid(state.token):
            return state.token  # type: ignore[return-value]
        if self.household_id:
            result = await self._request_broker_refresh(state.updated_at or 0)
            if result == "refreshed":
                renewed = self.load_state()
                if renewed.token and renewed.updated_at and renewed.updated_at > (state.updated_at or 0):
                    return renewed.token
            if result != "interaction_required":
                raise AuthError("Kurv kunne ikke forny Samsung Food lige nu. Prøv igen om lidt.")
        raise AuthInteractionRequired(
            "Samsung Food skal forbindes igen via Kurvs integrationsflow."
        )

    async def _request_broker_refresh(self, observed_at: float) -> str:
        base = os.getenv("SAMSUNG_LOGIN_BROKER_INTERNAL_BASE", "http://samsung-login-broker:8090")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    base.rstrip("/") + "/internal/samsung-auth/refresh",
                    headers={"X-Kurv-Broker-Key": broker_key()},
                    json={"household_id": self.household_id, "observed_at": observed_at},
                )
            response.raise_for_status()
            return response.json()["status"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise AuthError("Kurv kunne ikke forny Samsung Food lige nu. Prøv igen om lidt.") from exc


async def family_samsung_client(context: HouseholdContext) -> Any | None:
    """Return the request-safe Samsung client for one household.

    A completed family-scoped broker login always takes precedence, including
    for Familien Bagger. The historic core connector remains a compatibility
    fallback only while the legacy household has not yet completed the isolated
    Samsung login/list-selection flow.
    """

    household = await read_household(context)
    integration = household.get("integrations", {}).get("samsung_food", {})
    list_id = integration.get("list_id")
    auth_state = integration.get("auth_state_path")
    browser_profile = integration.get("browser_profile_path")

    if all(
        isinstance(value, str) and value
        for value in (list_id, auth_state, browser_profile)
    ):
        auth = RequestOnlySamsungAuthManager(
            household_id=context.household_id,
            state_file=Path(auth_state),
            browser_user_data_dir=Path(browser_profile),
            allow_credential_fallback=False,
        )
        return SamsungFoodClient(list_id=list_id, auth=auth)

    if context.household_id == LEGACY_HOUSEHOLD_ID:
        return None

    return None


__all__ = ["RequestOnlySamsungAuthManager", "family_samsung_client"]

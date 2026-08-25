import asyncio

import pytest

import app.main as core
from app.auth import AuthInteractionRequired, AuthState
from app.samsung import SamsungFoodError


_NOT_FOUND = (
    'Samsung Food READ failed: HTTP 400: '
    '{"code":"shoppingList.notFound","error_code":"LIST_ERROR_NOT_FOUND",'
    '"message":"Shopping list not found"}'
)


class FakeAuth:
    def __init__(self, *, interaction_required: bool = False):
        self.state = AuthState(token="old-token", updated_at=1.0, source="test")
        self.refresh_calls: list[bool] = []
        self.interaction_required = interaction_required

    def load_state(self) -> AuthState:
        return AuthState(
            token=self.state.token,
            updated_at=self.state.updated_at,
            source=self.state.source,
        )

    async def get_token(self, force_refresh: bool = False) -> str:
        self.refresh_calls.append(force_refresh)
        if self.interaction_required:
            raise AuthInteractionRequired("CAPTCHA eller 2FA kræver brugerens hjælp")
        self.state = AuthState(token="fresh-token", updated_at=2.0, source="credentials")
        return "fresh-token"


class FakeClient:
    def __init__(self, auth: FakeAuth):
        self.auth = auth


def test_list_not_found_forces_one_auth_refresh_and_retries():
    auth = FakeAuth()
    client = FakeClient(auth)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SamsungFoodError(_NOT_FOUND)
        return {"ok": True}

    result = asyncio.run(core._run_samsung_operation(client, operation))

    assert result == {"ok": True}
    assert calls == 2
    assert auth.refresh_calls == [True]


def test_non_list_error_does_not_trigger_auth_refresh():
    auth = FakeAuth()
    client = FakeClient(auth)

    async def operation():
        raise SamsungFoodError("Samsung Food READ failed: HTTP 500")

    with pytest.raises(SamsungFoodError, match="HTTP 500"):
        asyncio.run(core._run_samsung_operation(client, operation))

    assert auth.refresh_calls == []


def test_interactive_auth_requirement_is_returned_as_reconnect_error():
    auth = FakeAuth(interaction_required=True)
    client = FakeClient(auth)

    async def operation():
        raise SamsungFoodError(_NOT_FOUND)

    with pytest.raises(SamsungFoodError, match="forbindes igen"):
        asyncio.run(core._run_samsung_operation(client, operation))

    assert auth.refresh_calls == [True]

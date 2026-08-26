import asyncio
import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import households
from app import mobile_main
from app.households import HouseholdContext
from app.mobile_main import SetCheckedRequest, app
from app.quick_add import MAX_RANKED_ITEMS, MINIMUM_PURCHASES, ranked_items
from app.samsung import SamsungItemNotFoundError


client = TestClient(app)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_family(name: str) -> str:
    response = client.post(
        "/api/mobile/v1/households/create",
        json={"household_name": name, "member_name": "Ejer"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def buy_once(token: str, name: str) -> str:
    wanted = " ".join(name.strip().split())
    assert client.post(
        "/api/mobile/v1/items",
        headers=auth(token),
        json={"name": name},
    ).status_code == 200
    items = client.get("/api/mobile/v1/list", headers=auth(token)).json()["items"]
    item_id = next(item["id"] for item in reversed(items) if item["name"] == wanted and not item["checked"])
    assert client.patch(
        f"/api/mobile/v1/items/{item_id}/checked",
        headers=auth(token),
        json={"checked": True},
    ).status_code == 200
    return item_id


def test_purchase_is_counted_only_after_confirmed_check_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    token = create_family("Familie A")

    assert client.post(
        "/api/mobile/v1/items", headers=auth(token), json={"name": "Mælk"}
    ).status_code == 200
    before = client.get("/api/mobile/v1/quick-add", headers=auth(token)).json()
    assert before["items"] == []

    item_id = client.get("/api/mobile/v1/list", headers=auth(token)).json()["items"][0]["id"]
    for checked in (True, True, False, True):
        assert client.patch(
            f"/api/mobile/v1/items/{item_id}/checked",
            headers=auth(token),
            json={"checked": checked},
        ).status_code == 200

    result = client.get("/api/mobile/v1/quick-add", headers=auth(token)).json()
    assert result["minimum_purchases"] == MINIMUM_PURCHASES
    assert result["items"] == [{
        "name": "Mælk", "purchase_count": 1, "rank": 1, "eligible": False,
    }]


def test_quick_add_history_is_family_scoped_and_eligible_at_three(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    first = create_family("Familie A")
    second = create_family("Familie B")

    for _ in range(3):
        buy_once(first, " Mælk ")
    buy_once(second, "Mælk")

    first_items = client.get("/api/mobile/v1/quick-add", headers=auth(first)).json()["items"]
    second_items = client.get("/api/mobile/v1/quick-add", headers=auth(second)).json()["items"]

    assert first_items[0]["purchase_count"] == 3
    assert first_items[0]["eligible"] is True
    assert second_items[0]["purchase_count"] == 1
    assert second_items[0]["eligible"] is False


def test_ranking_returns_only_dynamic_top_ten():
    household = {
        "quick_add": {
            "items": {
                f"vare {index}": {
                    "name": f"Vare {index}",
                    "purchase_count": index,
                    "last_purchased_at": index,
                }
                for index in range(1, 12)
            }
        }
    }

    result = ranked_items(household)

    assert len(result) == MAX_RANKED_ITEMS
    assert [item.purchase_count for item in result] == list(range(11, 1, -1))
    assert [item.eligible for item in result] == [True] * 9 + [False]


def test_samsung_acknowledgement_does_not_wait_for_purchase_learning(monkeypatch):
    learning_started = asyncio.Event()
    release_learning = asyncio.Event()

    class FakeSamsungClient:
        async def set_item_checked(self, item_id, checked, **kwargs):
            return {
                "grpc_status": 0,
                "item_id": item_id,
                "item_name": "Mælk",
                "checked": checked,
            }

    async def fake_family_client(_context):
        return FakeSamsungClient()

    async def blocked_record_purchase(_context, *, item_id, item_name):
        learning_started.set()
        await release_learning.wait()

    monkeypatch.setattr(mobile_main, "family_samsung_client", fake_family_client)
    monkeypatch.setattr(mobile_main, "record_purchase", blocked_record_purchase)

    context = HouseholdContext(
        household_id="family-bagger",
        household_name="Familien Bagger",
        member_name="Christoffer",
        role="owner",
        list_backend="samsung",
    )

    async def scenario():
        response = await asyncio.wait_for(
            mobile_main.set_mobile_item_checked(
                "item-123",
                SetCheckedRequest(checked=True),
                context,
            ),
            timeout=0.2,
        )
        await asyncio.wait_for(learning_started.wait(), timeout=0.2)
        assert response["ok"] is True
        assert response["item_name"] == "Mælk"
        assert mobile_main.purchase_recording_tasks
        release_learning.set()
        await asyncio.gather(*tuple(mobile_main.purchase_recording_tasks))

    asyncio.run(scenario())


def test_rebound_samsung_id_is_used_for_purchase_idempotency(monkeypatch):
    recorded = []

    class FakeSamsungClient:
        async def set_item_checked(self, item_id, checked, **kwargs):
            assert kwargs["fallback_name"] == "coca cola"
            return {
                "grpc_status": 0,
                "item_id": "new-samsung-id",
                "item_name": "coca cola",
                "rebound": True,
            }

    async def fake_family_client(_context):
        return FakeSamsungClient()

    async def fake_record_purchase(_context, *, item_id, item_name):
        recorded.append((item_id, item_name))

    monkeypatch.setattr(mobile_main, "family_samsung_client", fake_family_client)
    monkeypatch.setattr(mobile_main, "record_purchase", fake_record_purchase)
    context = HouseholdContext(
        household_id="family-bagger",
        household_name="Familien Bagger",
        member_name="Christoffer",
        role="owner",
        list_backend="samsung",
    )

    async def scenario():
        response = await mobile_main.set_mobile_item_checked(
            "stale-samsung-id",
            SetCheckedRequest(checked=True, item_name="coca cola"),
            context,
        )
        await asyncio.gather(*tuple(mobile_main.purchase_recording_tasks))
        assert response["item_id"] == "new-samsung-id"
        assert response["rebound"] is True

    asyncio.run(scenario())
    assert recorded == [("new-samsung-id", "coca cola")]


def test_missing_samsung_item_is_a_non_transient_conflict(monkeypatch):
    class MissingSamsungClient:
        async def set_item_checked(self, item_id, checked, **kwargs):
            raise SamsungItemNotFoundError(f"Shopping item not found: {item_id}")

    async def fake_family_client(_context):
        return MissingSamsungClient()

    monkeypatch.setattr(mobile_main, "family_samsung_client", fake_family_client)
    context = HouseholdContext(
        household_id="family-bagger",
        household_name="Familien Bagger",
        member_name="Christoffer",
        role="owner",
        list_backend="samsung",
    )

    async def scenario():
        try:
            await mobile_main.set_mobile_item_checked(
                "gone-item",
                SetCheckedRequest(checked=True, item_name="coca cola"),
                context,
            )
        except mobile_main.HTTPException as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("Expected stale Samsung item conflict")

    asyncio.run(scenario())

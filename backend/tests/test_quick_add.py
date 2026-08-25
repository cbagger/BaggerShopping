import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app import households
from app.mobile_main import app
from app.quick_add import MAX_RANKED_ITEMS, MINIMUM_PURCHASES, ranked_items


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

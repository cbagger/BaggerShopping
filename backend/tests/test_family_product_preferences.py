import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app.households import HouseholdContext, set_current
from app.mobile_main import app
from app.product_identity import apply_family_preference, compare


client = TestClient(app)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_preferences_are_private_per_family_and_can_be_required(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    first = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie A", "member_name": "A"}).json()
    second = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie B", "member_name": "B"}).json()

    saved = client.put(
        "/api/mobile/v1/product-identity/preferences",
        headers=auth(first["access_token"]),
        json={"item_name": "Mælk", "preferred_name": "Arla letmælk", "mode": "required"},
    )
    assert saved.status_code == 200
    assert len(client.get("/api/mobile/v1/product-identity/preferences", headers=auth(first["access_token"])).json()["preferences"]) == 1
    assert client.get("/api/mobile/v1/product-identity/preferences", headers=auth(second["access_token"])).json()["preferences"] == []

    set_current(HouseholdContext(
        household_id=first["household_id"], household_name="Familie A",
        member_name="A", role="owner", list_backend="local",
    ))
    base = compare("Mælk", "Arla sødmælk")
    score, result = apply_family_preference("Mælk", "Arla sødmælk", 65, base)
    assert score == 0
    assert result.level == "not_same"
    assert "familiens krævede" in result.explanation

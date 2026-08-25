import os

os.environ.setdefault("MOBILE_API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from app.households import HouseholdContext, set_current
from app.mobile_main import app
from app.product_identity import apply_family_preference, compare, family_favorite_match


client = TestClient(app)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_favorites_are_private_per_family_and_never_filter_other_offers(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    first = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie A", "member_name": "A"}).json()
    second = client.post("/api/mobile/v1/households/create", json={"household_name": "Familie B", "member_name": "B"}).json()

    saved = client.put(
        "/api/mobile/v1/product-identity/preferences",
        headers=auth(first["access_token"]),
        json={"item_name": "Mælk", "preferred_name": "Arla letmælk 1 l", "mode": "favorite"},
    )
    assert saved.status_code == 200
    assert len(client.get("/api/mobile/v1/product-identity/preferences", headers=auth(first["access_token"])).json()["preferences"]) == 1
    assert client.get("/api/mobile/v1/product-identity/preferences", headers=auth(second["access_token"])).json()["preferences"] == []

    set_current(HouseholdContext(
        household_id=first["household_id"], household_name="Familie A",
        member_name="A", role="owner", list_backend="local",
    ))
    favorite = compare("Arla letmælk 1 l", "Arla letmælk 500 ml")
    score, result = apply_family_preference("Mælk", "Arla letmælk 500 ml", 65, favorite)
    assert score > 1_000
    assert result.level != "not_same"
    assert "foretrukne vare" in result.explanation

    other = compare("Mælk", "Coop sødmælk")
    other_score, other_result = apply_family_preference("Mælk", "Coop sødmælk", 65, other)
    assert other_score == 65
    assert other_result == other

    set_current(HouseholdContext(
        household_id=second["household_id"], household_name="Familie B",
        member_name="B", role="owner", list_backend="local",
    ))
    assert family_favorite_match("Arla letmælk 500 ml") is None


def test_favorite_match_ignores_amount_but_keeps_brand_and_variant(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    family = client.post(
        "/api/mobile/v1/households/create",
        json={"household_name": "Familie A", "member_name": "A"},
    ).json()
    token = family["access_token"]

    response = client.put(
        "/api/mobile/v1/product-identity/preferences",
        headers=auth(token),
        json={"item_name": "Ketchup", "preferred_name": "Beauvais ketchup 1 kg", "mode": "favorite"},
    )
    assert response.status_code == 200

    set_current(HouseholdContext(
        household_id=family["household_id"], household_name="Familie A",
        member_name="A", role="owner", list_backend="local",
    ))
    one_kilo = family_favorite_match("Beauvais ketchup 1 kg")
    smaller = family_favorite_match("Beauvais ketchup 500 ml")

    assert one_kilo is not None
    assert smaller is not None
    assert one_kilo.score > smaller.score
    assert family_favorite_match("Heinz ketchup 1 kg") is None


def test_favorite_packaging_is_a_tiebreaker_not_a_requirement(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    family = client.post(
        "/api/mobile/v1/households/create",
        json={"household_name": "Familie A", "member_name": "A"},
    ).json()
    client.put(
        "/api/mobile/v1/product-identity/preferences",
        headers=auth(family["access_token"]),
        json={"item_name": "Tuborg", "preferred_name": "Tuborg Grøn 30 x 33 cl glas", "mode": "favorite"},
    )
    set_current(HouseholdContext(
        household_id=family["household_id"], household_name="Familie A",
        member_name="A", role="owner", list_backend="local",
    ))

    glass = family_favorite_match("Tuborg Grøn 24 x 33 cl glas")
    cans = family_favorite_match("Tuborg Grøn 24 x 33 cl dåser")

    assert glass is not None
    assert cans is not None
    assert glass.score > cans.score


def test_legacy_required_preference_no_longer_filters_other_offers(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUSEHOLD_STORE_PATH", str(tmp_path / "households.json"))
    family = client.post(
        "/api/mobile/v1/households/create",
        json={"household_name": "Familie A", "member_name": "A"},
    ).json()
    client.put(
        "/api/mobile/v1/product-identity/preferences",
        headers=auth(family["access_token"]),
        json={"item_name": "Pepsi", "preferred_name": "Pepsi Max", "mode": "required"},
    )
    set_current(HouseholdContext(
        household_id=family["household_id"], household_name="Familie A",
        member_name="A", role="owner", list_backend="local",
    ))

    other = compare("Pepsi", "Pepsi Original")
    score, result = apply_family_preference("Pepsi", "Pepsi Original", 65, other)

    assert score == 65
    assert result == other

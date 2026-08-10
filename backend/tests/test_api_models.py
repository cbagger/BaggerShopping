from app.models import HomeAssistantShoppingResponse


def test_home_assistant_payload_shape():
    payload = HomeAssistantShoppingResponse(
        list_id="abc",
        name="Indkøbsliste",
        count=2,
        has_items=True,
        items=["mælk", "toiletpapir"],
    )
    assert payload.count == 2
    assert payload.has_items is True

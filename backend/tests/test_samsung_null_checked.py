from app.samsung import SamsungFoodClient


class DummyAuth:
    pass


def test_missing_checked_state_is_normalized_to_false():
    client = SamsungFoodClient(list_id="list-123", auth=DummyAuth())
    payload = {
        "list": {"name": "Indkøbsliste"},
        "content": {
            "items": [
                {"id": "item-1", "item": {"name": "Mælk"}},
                {"id": "item-2", "item": {"name": "Brød"}, "checked": True},
            ]
        },
    }

    normalized = client._normalize_list(payload)

    assert normalized.items[0].checked is False
    assert normalized.items[1].checked is True

import os

os.environ.setdefault("SAMSUNG_LIST_ID", "test-list")

from app.samsung import SamsungFoodClient


def test_extracts_natural_quantity_suffixes():
    cases = {
        "sødmælk x2": ("sødmælk", 2),
        "sødmælk × 2": ("sødmælk", 2),
        "sødmælk 2x": ("sødmælk", 2),
        "sødmælk 2 stk": ("sødmælk", 2),
        "sødmælk 2stk.": ("sødmælk", 2),
        "sødmælk 2 stykker": ("sødmælk", 2),
        "2x sødmælk": ("sødmælk", 2),
        "2x sødmælk ": ("sødmælk", 2),
        "3xbleer": ("bleer", 3),
        "3 stk bleer": ("bleer", 3),
        "3stk. bleer": ("bleer", 3),
    }
    for value, expected in cases.items():
        assert SamsungFoodClient._quantity_from_name(value) == expected


def test_does_not_guess_quantity_without_explicit_suffix():
    assert SamsungFoodClient._quantity_from_name("Coca-Cola 2 liter") == ("Coca-Cola 2 liter", None)
    assert SamsungFoodClient._quantity_from_name("iPhone 16") == ("iPhone 16", None)
    assert SamsungFoodClient._quantity_from_name("mælk x1") == ("mælk x1", None)
    assert SamsungFoodClient._quantity_from_name("1x mælk") == ("1x mælk", None)

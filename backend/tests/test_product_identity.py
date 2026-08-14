import asyncio

from app.product_identity import analyze, compare, product_feedback, FeedbackRequest


def test_extracts_brand_type_and_total_amount():
    product = analyze("Lurpak smørbar let saltet 2 x 200 g")

    assert product.brand == "lurpak"
    assert product.product == "smørbar"
    assert product.types == ["light"]
    assert product.total_amount == 400
    assert product.amount_dimension == "mass"


def test_zero_and_ordinary_cola_are_never_same_item():
    result = compare("Coca-Cola 1,5 l", "Coca-Cola Zero 1,5 l")

    assert result.level == "not_same"
    assert result.confidence >= .95


def test_light_is_visible_only_as_compatible_variant():
    result = compare("Lurpak smørbar", "Lurpak smørbar let")

    assert result.level == "compatible_variant"
    assert result.direct_price_comparison is False


def test_different_pack_sizes_are_compatible_but_not_directly_cheaper():
    result = compare("Lurpak smørbar 200 g", "Lurpak smørbar 500 g")

    assert result.level == "compatible_variant"
    assert result.direct_price_comparison is False


def test_equal_quantities_in_different_units_can_be_compared():
    result = compare("Arla mælk 1 l", "Arla mælk 1000 ml")

    assert result.level == "same_item"
    assert result.direct_price_comparison is True


def test_short_word_does_not_match_inside_unrelated_compound():
    assert compare("Æg", "Dansk pålæg").level == "not_same"


def test_feedback_is_global_and_never_match_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PRODUCT_IDENTITY_STORE_PATH", str(tmp_path / "identity.json"))
    request = FeedbackRequest(left="Lurpak smørbar", right="Lurpak let", decision="never_match")

    asyncio.run(product_feedback(request))

    assert compare(request.left, request.right).level == "not_same"
    assert "fælles produktviden" in compare(request.left, request.right).explanation

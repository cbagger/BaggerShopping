import pytest

from app import member_pricing_v4 as v4


@pytest.mark.parametrize(
    ("retailer", "text"),
    [
        (
            "Bilka",
            "PLUS PRIS Pr. stk. max. 1,98. Gælder kun med Bilka Plus appen. Frit valg 85 kr.",
        ),
        (
            "Netto",
            "+ PRIS Pr. stk. max. 1,98. Gælder kun med Netto+ appen. Frit valg 85 kr.",
        ),
        (
            "Lidl",
            "PLUS PRIS Pr. stk. max. 1,98. Gælder kun med Lidl Plus appen. Frit valg 85 kr.",
        ),
    ],
)
def test_unit_price_can_never_win_structured_member_precedence(monkeypatch, retailer, text):
    """A nearby member marker must not promote a per-item comparison price.

    This is the live Neophos failure shape, expressed without product-specific
    logic. When the structured interpretation is unsafe, v4 must fall through
    to the authoritative Luna pricing record rather than returning 1.98.
    """

    monkeypatch.setattr(
        v4.v3,
        "detect_member_pricing",
        lambda **_: v4.MemberPricing(
            ordinary_price=85.0,
            member_price=79.0,
            label=f"{retailer} Plus",
            app_name=f"{retailer} Plus",
            requires_activation=False,
            source="luna-verified",
            primary_price_was_member=False,
            confidence=0.99,
        ),
    )

    result = v4.detect_member_pricing(
        retailer=retailer,
        price=85.0,
        normal_price=None,
        text=text,
        unit_price="Pr. stk. max. 2,13",
    )

    assert result is not None
    assert result.ordinary_price == 85.0
    assert result.member_price == 79.0
    assert result.source == "luna-verified"


def test_real_member_price_still_wins_when_separate_unit_price_is_present(monkeypatch):
    """The guard must reject only the unit value, not valid explicit Plus prices."""

    def unexpected_luna(**_):
        raise AssertionError("safe explicit structured price should not need Luna fallback")

    monkeypatch.setattr(v4.v3, "detect_member_pricing", unexpected_luna)

    result = v4.detect_member_pricing(
        retailer="Bilka",
        price=85.0,
        normal_price=None,
        text=(
            "PLUS PRIS FRIT VALG 79,-. Pr. stk. max. 1,98. "
            "Gælder kun med Bilka Plus appen. Frit valg 85 kr."
        ),
        unit_price="Pr. stk. max. 2,13",
    )

    assert result is not None
    assert result.ordinary_price == 85.0
    assert result.member_price == 79.0
    assert result.source == "structured-explicit-member-price-v4"


def test_unit_price_value_parser_covers_stk_forms():
    assert v4._unit_price_values(
        "Pr. stk. max. 1,98",
        "1,98 kr/stk",
        "pr. stykker 2,13",
    ) == {1.98, 2.13}

from app.meny_flyer import Offer
from app.offer_serialization import customer_offer_payload, raw_offer_payload


def _offer() -> Offer:
    return Offer(
        id="offer-1",
        retailer="Bilka",
        publication_id="pub-1",
        publication_title="Bilka uge 34",
        product_name="Testvare",
        price=85.0,
        unit_price="Pr. stk. max. 1,98",
        source_url="https://example.test",
        raw_text=(
            "Testvare PLUS PRIS FRIT VALG 79,-. "
            "Pr. stk. max. 1,98. Gælder kun med Bilka Plus appen. "
            "Frit valg 85 kr."
        ),
    )


def test_raw_offer_payload_never_contains_customer_member_metadata():
    payload = raw_offer_payload(_offer())

    assert payload["price"] == 85.0
    assert "member_price" not in payload
    assert "member_price_label" not in payload
    assert "member_price_source" not in payload


def test_customer_offer_payload_adds_member_metadata_without_mutating_offer():
    offer = _offer()
    payload = customer_offer_payload(offer)

    assert offer.price == 85.0
    assert offer.normal_price is None
    assert payload["price"] == 85.0
    assert payload["member_price"] == 79.0
    assert payload["member_price_label"] == "Bilka Plus"
    assert payload["member_price_source"] == "structured-explicit-member-price-v4"

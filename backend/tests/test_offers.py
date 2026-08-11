import json

import pytest

from app.offers import fetch_goma_offers, goma_offer_url, parse_goma_html, slugify_query


def test_slugify_query_handles_danish_characters():
    assert slugify_query("Smør & Grønt") == "smoer-groent"
    assert goma_offer_url("Margarine").endswith("/dagligvarer/margarine/tilbud")


def test_parses_structured_json_for_meny_offer():
    payload = {
        "props": {
            "offers": [
                {
                    "retailer": {"name": "MENY"},
                    "productName": "Ama Stege & Bage Margarine",
                    "offerPrice": 12,
                    "normalPrice": 19.95,
                    "quantity": 500,
                    "unit": "g",
                    "unitPrice": "24,00 kr/kg",
                    "discountPercent": 40,
                },
                {
                    "retailer": {"name": "Netto"},
                    "productName": "Andet produkt",
                    "offerPrice": 10,
                },
            ]
        }
    }
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'

    offers, parser = parse_goma_html(html, "MENY")

    assert parser == "structured-json"
    assert len(offers) == 1
    offer = offers[0]
    assert offer.retailer == "MENY"
    assert offer.product_name == "Ama Stege & Bage Margarine"
    assert offer.price == 12
    assert offer.normal_price == 19.95
    assert offer.quantity == 500
    assert offer.unit == "g"
    assert offer.discount_percent == 40


def test_text_fallback_is_retailer_scoped():
    html = """
    <html><body>
      <article><span>MENY</span><span>12,00 kr</span><span>19,95 kr</span>
      <strong>Ama Stege &amp; Bage Margarine</strong><span>500 g</span><span>24,00 kr/kg</span></article>
      <article><span>Netto</span><span>10,00 kr</span><strong>Netto vare</strong></article>
    </body></html>
    """

    offers, parser = parse_goma_html(html, "MENY")

    assert parser == "html-text"
    assert len(offers) == 1
    assert offers[0].retailer == "MENY"
    assert offers[0].price == 12
    assert offers[0].normal_price == 19.95
    assert offers[0].quantity == 500
    assert offers[0].unit == "g"


@pytest.mark.asyncio
async def test_fetch_uses_normalized_result(httpx_mock):
    httpx_mock.add_response(
        url="https://goma.gg/dagligvarer/margarine/tilbud",
        html="""
        <html><body><article><span>MENY</span><span>12,00 kr</span><span>19,95 kr</span>
        <strong>Ama Margarine</strong><span>500 g</span></article></body></html>
        """,
    )

    # pytest-httpx injects its transport into normal httpx clients.
    result = await fetch_goma_offers("margarine", "MENY")
    assert result.ok is True
    assert result.retailer == "MENY"
    assert result.offers[0].product_name

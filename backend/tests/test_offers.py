import asyncio
import json

import httpx

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


def test_anchor_parser_is_retailer_scoped_and_stops_before_non_offers():
    html = """
    <html><body>
      <a href="/p/meny-offer"><span>-40%</span><span>MENY</span><span>12,00 kr</span><span>19,95 kr</span>
      <strong>Ama Stege &amp; Bage Margarine</strong><span>500 g</span><span>24,00 kr/kg</span></a>
      <a href="/p/netto-offer"><span>-20%</span><span>Netto</span><span>10,00 kr</span><span>12,50 kr</span><strong>Netto vare</strong></a>
      <h2>40 andre varer ikke på tilbud</h2>
      <a href="/p/meny-normal"><span>MENY</span><span>9,95 kr</span><strong>Normal MENY-vare</strong><span>500 g</span><span>19,90 kr/kg</span></a>
    </body></html>
    """

    offers, parser = parse_goma_html(html, "MENY")

    assert parser == "offer-anchors"
    assert len(offers) == 1
    assert offers[0].retailer == "MENY"
    assert offers[0].product_name == "Ama Stege & Bage Margarine"
    assert offers[0].price == 12
    assert offers[0].normal_price == 19.95
    assert offers[0].quantity == 500
    assert offers[0].unit == "g"
    assert offers[0].discount_percent == 40
    assert offers[0].product_url == "https://goma.gg/p/meny-offer"


def test_anchor_parser_returns_empty_when_retailer_has_only_normal_price_rows():
    html = """
    <html><body>
      <a href="/p/loevbjerg"><span>Tilbud</span><span>Løvbjerg</span><span>16,99 kr</span><strong>Oma Margarine</strong></a>
      <h2>40 andre varer ikke på tilbud</h2>
      <a href="/p/meny"><span>MENY</span><span>9,95 kr</span><strong>Fp Flyd.margarine</strong><span>500 ml</span><span>19,90 kr/liter</span></a>
    </body></html>
    """

    offers, parser = parse_goma_html(html, "MENY")

    assert parser == "offer-anchors"
    assert offers == []


def test_fetch_uses_normalized_result():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://goma.gg/dagligvarer/margarine/tilbud"
        return httpx.Response(
            200,
            text="""
            <html><body>
            <a href="/p/meny-offer"><span>-40%</span><span>MENY</span><span>12,00 kr</span><span>19,95 kr</span>
            <strong>Ama Margarine</strong><span>500 g</span><span>24,00 kr/kg</span></a>
            <h2>Andre varer ikke på tilbud</h2>
            </body></html>
            """,
            request=request,
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_goma_offers("margarine", "MENY", client=client)

    result = asyncio.run(run())
    assert result.ok is True
    assert result.retailer == "MENY"
    assert len(result.offers) == 1
    assert result.offers[0].product_name == "Ama Margarine"

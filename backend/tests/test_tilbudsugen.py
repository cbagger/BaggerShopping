import asyncio
from datetime import date

import httpx
import pytest
from fastapi import HTTPException

from app import mobile_offers, tilbudsugen


def _row(retailer, *, promotion=None, end="2099-12-28"):
    return {
        "product_name": "Hakket oksekød",
        "brand": "Eksempel",
        "currency": "DKK",
        "price": 29,
        "start_date": "2099-12-21T00:00:00Z",
        "end_date": f"{end}T23:59:59Z",
        "promotion": promotion or {},
        "unit_metrics": {"quantity": 0.4, "unit_type": "kg."},
        "availability": {"retail_chain": retailer, "is_border_shop": False},
    }


def test_live_search_adapter_uses_one_query_and_preserves_price_conditions(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url)
        return httpx.Response(200, json={"items": [{"offers": [
            _row("REMA 1000"),
            _row("365discount", promotion={"requiresMembership": True}),
            _row("Netto", promotion={"minQuantity": 2, "totalPrice": 50}),
            _row("Netto", end="2000-01-01"),
            _row("Lidl"),
        ]}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(tilbudsugen.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handle), **kw,
    ))
    names = ["365discount", "REMA 1000", "Netto"]
    result = asyncio.run(tilbudsugen.fetch_offers("oksekød", names))
    assert len(calls) == 1
    assert calls[0].params["chains"] == "365discount,rema_1000,netto"
    assert [offer.retailer for offer, _ in result] == ["REMA 1000", "365discount", "Netto"]
    assert all(status == "upcoming" for _, status in result)
    assert all(offer.valid_until == "28.12.2099" for offer, _ in result)

    ordinary, member, bulk = [mobile_offers._offer_payload(offer) for offer, _ in result]
    assert ordinary["price"] == 29 and ordinary["safe_to_add"]
    assert ordinary["quantity"] == 0.4 and ordinary["unit"] == "kg"
    assert member["price"] is None and member["member_price"] == 29 and not member["safe_to_add"]
    assert bulk["price"] == 50 and bulk["minimum_purchase_quantity"] == 2 and not bulk["safe_to_add"]


def test_mobile_search_opt_in_official_links_and_provider_failure(monkeypatch):
    async def empty_catalogue():
        return []

    calls = []

    async def search(term, retailers):
        calls.append((term, retailers))
        row = _row(retailers[0])
        return [(tilbudsugen._parse_offer(row, set(retailers), date.today()), "upcoming")]

    monkeypatch.setattr(mobile_offers, "_publications", empty_catalogue)
    monkeypatch.setattr(tilbudsugen, "fetch_offers", search)
    catalogue = asyncio.run(mobile_offers.publications())
    assert catalogue["external_search_retailers"] == ["365discount", "REMA 1000", "Netto"]
    assert all(row["url"].startswith("https://") for row in catalogue["external_flyers"])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mobile_offers.search_offers(q="oksekød", retailer="Netto"))
    assert exc.value.status_code == 404
    assert calls == []

    result = asyncio.run(mobile_offers.search_offers(q="oksekød", retailer="Netto", include_external=True))
    assert calls == [("oksekød", ["Netto"])]
    assert result["retailer"] == "Netto"
    assert result["offers"][0]["price"] == 29
    assert result["offers"][0]["publication_status"] == "upcoming"

    async def down(term, retailers):
        raise httpx.ReadTimeout("test")

    monkeypatch.setattr(tilbudsugen, "fetch_offers", down)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mobile_offers.search_offers(q="oksekød", retailer="Netto", include_external=True))
    assert exc.value.status_code == 503

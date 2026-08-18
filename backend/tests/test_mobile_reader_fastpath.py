import asyncio

import pytest
from fastapi import HTTPException

from app import mobile_offers
from app.meny_flyer import Offer, OfferVariant, Publication


def sample_offer() -> Offer:
    return Offer(
        id="offer-1",
        retailer="MENY",
        publication_id="pub-1",
        publication_title="MENY uge 34",
        product_name="Kakaomælk",
        price=10.0,
        source_url="https://example.test/offer",
        raw_text="Kakaomælk 1 l 10 kr",
        page_number=2,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.15,
        hotspot_confidence=0.95,
        variants=[OfferVariant(id="variant-1", name="Kakaomælk")],
    )


def sample_publication() -> Publication:
    return Publication(
        id="pub-1",
        retailer="MENY",
        title="MENY uge 34",
        source_url="https://example.test/flyer",
        page_count=2,
        page_image_urls=["https://example.test/1.jpg", "https://example.test/2.jpg"],
        structured_offers=[sample_offer()],
    )


def test_explicit_reader_payload_keeps_hotspot_and_variants_without_product_identity(monkeypatch):
    monkeypatch.setattr(mobile_offers, "load_feedback_store", lambda _: {})

    payload = mobile_offers._offer_payload(sample_offer(), include_identity=False)

    assert payload["hotspot_x"] == 0.1
    assert payload["hotspot_y"] == 0.2
    assert payload["hotspot_width"] == 0.3
    assert payload["hotspot_height"] == 0.15
    assert payload["variants"][0]["name"] == "Kakaomælk"
    assert "product_identity" not in payload
    assert "identity_match" not in payload


def test_publication_reader_endpoint_does_not_run_product_identity(monkeypatch):
    publication = sample_publication()

    async def publications():
        return [publication]

    def forbidden_analyze(*args, **kwargs):
        raise AssertionError("flyer reader must not recompute product identity")

    monkeypatch.setattr(mobile_offers, "_publications", publications)
    monkeypatch.setattr(mobile_offers, "load_feedback_store", lambda _: {})
    monkeypatch.setattr(mobile_offers, "analyze", forbidden_analyze)

    response = asyncio.run(mobile_offers.publication_offers(publication.id))

    assert len(response["offers"]) == 1
    assert response["offers"][0]["hotspot_x"] == 0.1
    assert "product_identity" not in response["offers"][0]


def test_stale_publication_id_never_falls_back_to_current_meny(monkeypatch):
    publication = sample_publication()

    async def publications():
        return [publication]

    async def forbidden_current_publication():
        raise AssertionError("stale flyer id must never substitute another publication")

    monkeypatch.setattr(mobile_offers, "_publications", publications)
    monkeypatch.setattr(mobile_offers, "_publication", forbidden_current_publication)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mobile_offers.publication_offers("stale-publication-id"))

    assert exc.value.status_code == 404
    assert "findes ikke længere" in str(exc.value.detail)

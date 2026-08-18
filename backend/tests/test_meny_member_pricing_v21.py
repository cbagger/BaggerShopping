import pytest

from app.member_pricing_sources import enrich_ipaper_offers
from app.meny_flyer import Offer, Publication
from app.offer_serialization import customer_offer_payload


def _publication(page_text: str) -> Publication:
    return Publication(
        id="meny-current",
        retailer="MENY",
        title="MENY uge 34",
        valid_from="14.08.2026",
        valid_until="20.08.2026",
        status="current",
        source_url="https://ugensavis.meny.dk/",
        page_count=1,
        page_image_urls=["https://cdn.test/Pages/1/Normal.jpg"],
        page_texts=[page_text],
    )


def _offer(name: str, price: float, *, variants=()) -> Offer:
    return Offer(
        id="offer-1",
        retailer="MENY",
        publication_id="meny-current",
        publication_title="MENY uge 34",
        valid_from="14.08.2026",
        valid_until="20.08.2026",
        product_name=name,
        price=price,
        source_url="https://ugensavis.meny.dk/",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        raw_text=" | ".join(variants) if variants else name,
        quality_score=0.99,
    )


@pytest.mark.parametrize(
    ("name", "ordinary", "member", "page_text"),
    [
        (
            "Quickbury Fastfood Buns",
            14.0,
            9.95,
            "QUICKBURY FASTFOOD BUNS Hot Dog Buns, Hamburger Buns eller Mega Burger Buns. "
            "250-300 g. Max. kg pris 56,00. PR. PAKKE 14,- MEDLEMSPRIS* PR. PAKKE 9,95 "
            "Max. 4 pakker pr. kunde",
        ),
        (
            "God Morgen Økologisk Juice",
            28.0,
            19.95,
            "GOD MORGEN ØKOLOGISK JUICE Flere varianter. 850 ml. Literpris 32,94. "
            "PR. FLASKE 28,- + pant MEDLEMSPRIS* PR. FLASKE 19,95 + pant",
        ),
        (
            "Pågen Kanel Gifflar eller Vanillas",
            15.0,
            8.95,
            "PÅGEN KANEL GIFFLAR ELLER VANILLAS 220-300 g. Max. kg pris 68,18. "
            "PR. POSE 15,- MEDLEMSPRIS* PR. POSE 8,95",
        ),
    ],
)
def test_exact_meny_ipaper_price_pair_is_customer_visible_without_luna(
    name, ordinary, member, page_text
):
    source = _offer(name, ordinary)
    enriched = enrich_ipaper_offers(_publication(page_text), [source])[0]

    payload = customer_offer_payload(enriched)

    assert payload["price"] == ordinary
    assert payload["member_price"] == member
    assert payload["member_price_label"] == "MENY medlemspris"
    assert payload["member_price_app"] == "MENY-appen"
    assert payload["member_price_source"].startswith("structured-")


def test_meny_exact_context_still_rejects_neighbour_member_price_contamination():
    publication = _publication(
        "GM JUICE 16,-. PÅGEN KANEL GIFFLAR ELLER VANILLAS 220-300 g. "
        "PR. POSE 15,- MEDLEMSPRIS* PR. POSE 8,95. GESTUS POMMES 20,-."
    )
    source = _offer("GM Juice", 16.0)

    enriched = enrich_ipaper_offers(publication, [source])[0]
    payload = customer_offer_payload(enriched)

    assert payload["price"] == 16.0
    assert "member_price" not in payload


def test_meny_exact_context_requires_provider_ordinary_price_before_member_marker():
    publication = _publication(
        "QUICKBURY FASTFOOD BUNS 250-300 g. 12,- PR. PAKKE 14,- "
        "MEDLEMSPRIS* PR. PAKKE 9,95"
    )
    source = _offer("Quickbury Fastfood Buns", 14.0)

    enriched = enrich_ipaper_offers(publication, [source])[0]
    payload = customer_offer_payload(enriched)

    # The extra 12,- between the product anchor and the explicit member role
    # makes the local price ordering ambiguous. Fail closed rather than assign
    # the wrong price pair automatically.
    assert payload["price"] == 14.0
    assert "member_price" not in payload

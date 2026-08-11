import asyncio

import httpx
import pytest

from app.meny_flyer import fetch_meny_flyer, parse_enrichment_chunks, parse_meny_flyer_html, search_publication


HTML = """
<html><body>
<h1>MENY uge 3326</h1>
<p>Avisen gælder fra fredag 07.08.2026 til og med torsdag 13.08.2026.</p>
<p>VALSØLILLE DANSK JUICE, DRIK ELLER SMOOTHIE Flere varianter. 850 ml. Literpris 32,94. Køl PR. FLASKE 28.- + pant</p>
<p>VANDMELON Udenlandsk. PR. STK. 25.-</p>
</body></html>
"""

IPAPER_HTML = r'''
<html><head><script>
window.viewerState = {\"pageTexts\":[\"VANDMELON Udenlandsk. PR. STK. 25.- SKARP PRIS\",\"VALSØLILLE DANSK JUICE, DRIK ELLER SMOOTHIE Flere varianter. 850 ml. Literpris 32,94. PR. FLASKE 28.- + pant\"]};
window.dataStore = {\"flipbookName\":\"MENY uge 3326\"};
</script></head><body>
<h1>MENY uge 3326</h1>
<p>Avisen gælder fra fredag 07.08.2026 til og med torsdag 13.08.2026.</p>
</body></html>
'''


def test_parse_publication_metadata():
    publication = parse_meny_flyer_html(HTML)
    assert publication.title == "MENY uge 3326"
    assert publication.week == 33
    assert publication.year == 2026
    assert publication.valid_from == "07.08.2026"
    assert publication.valid_until == "13.08.2026"


def test_search_finds_product_text_from_current_flyer():
    publication = parse_meny_flyer_html(HTML)
    result = search_publication(publication, "JUICE")
    assert result.matches
    assert "VALSØLILLE" in result.matches[0]
    assert "850 ml" in result.matches[0]
    assert "28.-" in result.matches[0]
    assert result.offers[0].product_name
    assert result.offers[0].price == 28
    assert result.offers[0].retailer == "MENY"


def test_search_returns_empty_for_missing_term():
    publication = parse_meny_flyer_html(HTML)
    result = search_publication(publication, "margarine")
    assert result.matches == []


def test_ipaper_page_texts_are_preferred_over_serialized_script_noise():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    assert publication.content_source == "ipaper-pageTexts"
    assert publication.page_count == 2
    assert "VANDMELON" in publication.text
    assert "VALSØLILLE" in publication.text
    assert "GoogleTagManager" not in publication.text

    result = search_publication(publication, "vandmelon")
    assert len(result.matches) == 1
    assert "PR. STK. 25.-" in result.matches[0]
    assert result.offers[0].page_number == 1
    assert result.offers[0].publication_id == publication.id
    assert result.offers[0].safe_to_add is True


def test_publication_exposes_generic_reader_metadata():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    assert publication.reader_url == "https://ugensavis.meny.dk/"
    assert publication.reader_kind == "embedded-viewer"
    assert publication.status in {"current", "upcoming", "expired"}


def test_search_uses_package_price_instead_of_unit_price():
    publication = parse_meny_flyer_html(
        "<p>MENY uge 3326</p><p>HAKKET OKSEKØD 600 G. Max. kg pris 116,58. PR. PAKKE 69 95</p>"
    )
    offer = search_publication(publication, "oksekød").offers[0]
    assert offer.price == 69.95


def test_validity_is_not_tied_to_specific_weekdays():
    html = """
    <html><body><h1>MENY uge 3326</h1>
    <p>Avisen gælder fra tirsdag 11.08.2026 til og med søndag 16.08.2026.</p>
    <p>TESTVARE PR. STK. 10.-</p></body></html>
    """
    publication = parse_meny_flyer_html(html)
    assert publication.valid_from == "11.08.2026"
    assert publication.valid_until == "16.08.2026"


def test_fetch_meny_flyer_uses_official_source():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ugensavis.meny.dk/"
        return httpx.Response(200, text=HTML, request=request)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_meny_flyer(client=client)

    publication = asyncio.run(run())
    assert publication.week == 33
    assert "VALSØLILLE" in publication.text


def test_structured_enrichments_group_variants_and_use_package_price():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    chunks = [{"enrichments": [
        {
            "type": 13,
            "pageIndex": 8,
            "productId": "beef",
            "name": "Hakket Kødkvæg 14-18% (Kyllingeunderlår eller Hakket Oksekød)",
            "alttext": "Kyllingeunderlår eller Hakket Oksekød",
            "desc": "Hakket Kødkvæg 14-18%. 600 g (Max. kg pris 116,58)",
            "price": 69.95,
        },
        {
            "type": 13,
            "pageIndex": 8,
            "productId": "chicken",
            "name": "Kylling Underlår (Kyllingeunderlår eller Hakket Oksekød)",
            "alttext": "Kyllingeunderlår eller Hakket Oksekød",
            "desc": "Kylling Underlår. 2000 g (Max. kg pris 34,98)",
            "price": 69.95,
        },
        {"type": 6, "pageIndex": 8, "alttext": "decorative hotspot"},
    ]}]
    publication.structured_offers = parse_enrichment_chunks(publication, chunks)

    result = search_publication(publication, "oksekød")

    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.price == 69.95
    assert offer.page_number == 9
    assert [variant.name for variant in offer.variants] == ["Hakket oksekød 14-18%"]
    assert offer.variants[0].quantity == 600
    assert offer.variants[0].matches_query is True
    assert offer.safe_to_add is True


def test_structured_search_ignores_descriptions_and_raw_advert_copy():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 1, "productId": "dog-food", "name": "Whiskas Fjerkræ",
        "alttext": "Whiskas eller Frolic", "desc": "Serveres ikke sammen med oksekød", "price": 25,
    }]}])

    assert search_publication(publication, "oksekød").offers == []


def test_generic_meat_search_excludes_pet_food_flavour_but_pet_search_still_works():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 1, "productId": "cat-beef", "name": "Whiskas 1+ Oksekød",
        "alttext": "Whiskas eller Frolic", "desc": "Kattemad med oksekød", "price": 25,
    }]}])

    assert search_publication(publication, "oksekød").offers == []
    assert len(search_publication(publication, "Whiskas").offers) == 1


def test_implausible_quantity_is_omitted_instead_of_guessed():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    offers = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "melon", "name": "Vandmelon",
        "alttext": "Vandmelon", "desc": "Vandmelon. 17 kg. Pr. stk.", "price": 25,
    }]}])

    assert offers[0].quantity is None
    assert offers[0].unit is None


def test_publication_page_count_uses_ipaper_pages_not_text_layer_count():
    html = IPAPER_HTML.replace(
        "window.viewerState =",
        'window.staticSettings = {"pages":[1,2,3,4],"aws":{"url":"https://cdn.test/paper/","policy":"signed=yes"},"enrichments":{"chunkUrls":{"1-4":"https://example.test/chunk.json"}}}; window.viewerState =',
    )
    publication = parse_meny_flyer_html(html)
    assert publication.page_count == 4
    assert publication.enrichment_urls == ["https://example.test/chunk.json"]
    assert publication.page_image_urls[0] == "https://cdn.test/paper/Pages/1/Normal.jpg?signed=yes"


def test_structured_offer_exposes_native_hotspot_geometry():
    publication = parse_meny_flyer_html(
        IPAPER_HTML.replace(
            "window.viewerState =",
            'window.staticSettings = {"pages":[1],"aws":{"url":"https://cdn.test/paper"}}; window.viewerState =',
        )
    )
    offer = parse_enrichment_chunks(publication, [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "milk", "name": "Kakaomælk",
        "alttext": "Kakaomælk", "desc": "1 l", "price": 9.95,
        "x": 0.4, "y": 0.7, "width": 0.1, "height": 0.08,
    }]}])[0]
    assert (offer.hotspot_x, offer.hotspot_y) == (0.4, 0.7)
    assert offer.hotspot_width == pytest.approx(0.1)
    assert offer.hotspot_height == pytest.approx(0.08)
    assert offer.image_url == "https://cdn.test/paper/Pages/1/Normal.jpg"


def test_type_6_marker_supplies_geometry_to_type_13_variants_by_parent_id():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {
            "type": 6, "id": 9001, "pageIndex": 8,
            "alttext": "Kyllingeunderlår eller Hakket Oksekød",
            "x": 0.42, "y": 0.61, "width": 0.106, "height": 0.073,
        },
        {
            "type": 13, "id": 1001, "parentid": 9001, "pageIndex": 8,
            "productId": "beef", "name": "Hakket Kødkvæg",
            "alttext": "Kyllingeunderlår eller Hakket Oksekød",
            "desc": "600 g", "price": 69.95,
        },
        {
            "type": 13, "id": 1002, "parentid": 9001, "pageIndex": 8,
            "productId": "chicken", "name": "Kylling Underlår",
            "alttext": "Kyllingeunderlår eller Hakket Oksekød",
            "desc": "2000 g", "price": 69.95,
        },
    ]}])

    assert len(offers) == 1
    assert len(offers[0].variants) == 2
    assert (offers[0].hotspot_x, offers[0].hotspot_y) == pytest.approx((0.42, 0.61))
    assert (offers[0].hotspot_width, offers[0].hotspot_height) == pytest.approx((0.106, 0.073))


def test_type_6_marker_falls_back_to_page_and_label_when_parent_id_is_missing():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    offer = parse_enrichment_chunks(publication, [{"enrichments": [
        {
            "type": 6, "id": 9001, "pageIndex": 1, "alttext": "Kakaomælk",
            "x": 0.2, "y": 0.3, "width": 0.1, "height": 0.08,
        },
        {
            "type": 13, "id": 1001, "pageIndex": 1, "productId": "milk",
            "name": "Kakaomælk", "alttext": "Kakaomælk", "price": 9.95,
        },
    ]}])[0]

    assert (offer.hotspot_x, offer.hotspot_y) == pytest.approx((0.2, 0.3))


def test_structured_offer_accepts_string_percentage_and_nested_geometry():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {
            "type": 13, "pageIndex": "1", "productId": "milk", "name": "Kakaomælk",
            "alttext": "Kakaomælk", "desc": "1 l", "price": "9,95",
            "x": "40", "y": "70", "width": "10", "height": "8",
        },
        {
            "type": 13, "pageIndex": 2, "productId": "rye", "name": "Rugbrød",
            "alttext": "Rugbrød", "desc": "500 g", "price": 18,
            "bounds": {"left": 0.2, "top": 0.3, "w": 0.25, "h": 0.15},
        },
    ]}])

    assert offers[0].page_number == 2
    assert offers[0].price == pytest.approx(9.95)
    assert (offers[0].hotspot_x, offers[0].hotspot_y) == pytest.approx((0.4, 0.7))
    assert (offers[1].hotspot_width, offers[1].hotspot_height) == pytest.approx((0.25, 0.15))


def test_per_piece_product_does_not_inherit_stray_weight():
    assert parse_enrichment_chunks(parse_meny_flyer_html(IPAPER_HTML), [{"enrichments": [{
        "type": 13, "pageIndex": 0, "productId": "melon", "name": "Vandmelon",
        "alttext": "Vandmelon", "desc": "Melon vand 17 kg. 1 stk (Stk. pris 25,00)", "price": 25,
    }]}])[0].quantity is None


def test_grocery_domains_resolve_semantic_name_collisions():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 0, "productId": "fish", "name": "Panerede fiskefileter", "alttext": "Fiskefileter", "desc": "400 g", "price": 25},
        {"type": 13, "pageIndex": 1, "productId": "candy", "name": "Katjes Salte Fisk", "alttext": "Slikposer", "desc": "100 g", "price": 15},
        {"type": 13, "pageIndex": 2, "productId": "milk", "name": "Skummetmælk", "alttext": "Mælk", "desc": "1 l", "price": 10},
        {"type": 13, "pageIndex": 3, "productId": "choc", "name": "Ritter Sport Mælk", "alttext": "Chokolade", "desc": "100 g", "price": 20},
        {"type": 13, "pageIndex": 4, "productId": "sauce", "name": "Hvid mælkesauce", "alttext": "Sauce", "desc": "500 ml", "price": 16},
    ]}])

    assert [offer.product_name for offer in search_publication(publication, "fisk").offers] == ["Fiskefileter"]
    assert [offer.product_name for offer in search_publication(publication, "mælk").offers] == ["Mælk"]


def test_search_returns_only_matching_variants_from_campaign_family():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 0, "productId": "rye", "name": "Levebrød Sandwichrugbrød", "alttext": "Schulstad-brød", "desc": "500 g", "price": 18},
        {"type": 13, "pageIndex": 0, "productId": "wheat", "name": "Levebrød Hvedebrød", "alttext": "Schulstad-brød", "desc": "500 g", "price": 18},
    ]}])

    offers = search_publication(publication, "rugbrød").offers

    assert len(offers) == 1
    assert [variant.name for variant in offers[0].variants] == ["Levebrød Sandwichrugbrød"]
    assert all(variant.matches_query for variant in offers[0].variants)


def test_category_alias_finds_soda_brands_without_unrelated_variants():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 0, "productId": "cola", "name": "Coca-Cola Zero", "alttext": "Drikkevarer", "desc": "1,5 l", "price": 9.95},
        {"type": 13, "pageIndex": 0, "productId": "juice", "name": "Appelsinjuice", "alttext": "Drikkevarer", "desc": "1 l", "price": 9.95},
        {"type": 13, "pageIndex": 1, "productId": "cookie", "name": "Colakage", "alttext": "Småkager", "desc": "200 g", "price": 14},
    ]}])

    offers = search_publication(publication, "sodavand").offers

    assert len(offers) == 1
    assert [variant.name for variant in offers[0].variants] == ["Coca-Cola Zero"]


def test_literal_query_uses_word_boundaries_not_substrings():
    publication = parse_meny_flyer_html(IPAPER_HTML)
    publication.structured_offers = parse_enrichment_chunks(publication, [{"enrichments": [
        {"type": 13, "pageIndex": 0, "productId": "cola", "name": "Coca-Cola Zero", "alttext": "Sodavand", "desc": "1,5 l", "price": 9.95},
        {"type": 13, "pageIndex": 1, "productId": "chocolate", "name": "Magnum Chocolate White", "alttext": "Is", "desc": "4 stk", "price": 30},
    ]}])

    offers = search_publication(publication, "cola").offers

    assert len(offers) == 1
    assert [variant.name for variant in offers[0].variants] == ["Coca-Cola Zero"]

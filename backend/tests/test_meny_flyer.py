import asyncio

import httpx

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
    assert [variant.name for variant in offer.variants] == ["Hakket Kødkvæg 14-18%", "Kylling Underlår"]
    assert offer.variants[0].quantity == 600
    assert offer.safe_to_add is True


def test_publication_page_count_uses_ipaper_pages_not_text_layer_count():
    html = IPAPER_HTML.replace(
        "window.viewerState =",
        'window.staticSettings = {"pages":[1,2,3,4],"enrichments":{"chunkUrls":{"1-4":"https://example.test/chunk.json"}}}; window.viewerState =',
    )
    publication = parse_meny_flyer_html(html)
    assert publication.page_count == 4
    assert publication.enrichment_urls == ["https://example.test/chunk.json"]

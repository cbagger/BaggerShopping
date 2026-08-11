import asyncio

import httpx

from app.meny_flyer import fetch_meny_flyer, parse_meny_flyer_html, search_publication


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

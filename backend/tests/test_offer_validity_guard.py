from __future__ import annotations

from datetime import date

from app.flyer_publications import _apply_tjek_offer_validity, _tjek_offer_validity
from app.luna_offer_validity import (
    extend_fact_schema,
    page_fingerprint,
    page_has_explicit_validity_marker,
    safe_offer_validity,
    starts_in_future,
    validate_page_rows,
)
from app.meny_flyer import Offer, Publication
from app.offer_serialization import customer_offer_payload, offer_is_upcoming


def _offer(**updates) -> Offer:
    values = {
        "id": "offer-123-28",
        "retailer": "365discount",
        "publication_id": "pub-1",
        "publication_title": "Uge 34",
        "valid_from": "20.08.2026",
        "valid_until": "26.08.2026",
        "product_name": "Coop bacon i skiver",
        "price": 10.0,
        "image_url": "https://example.test/page.jpg",
        "source_url": "https://example.test/flyer",
        "page_number": 28,
        "hotspot_x": 0.5,
        "hotspot_y": 0.5,
        "hotspot_width": 0.2,
        "hotspot_height": 0.2,
        "raw_text": "Coop bacon i skiver eller i tern",
        "safe_to_add": True,
    }
    values.update(updates)
    return Offer(**values)


def _publication(page_text: str = "") -> Publication:
    return Publication(
        id="pub-1",
        retailer="365discount",
        title="Uge 34",
        valid_from="20.08.2026",
        valid_until="26.08.2026",
        status="current",
        source_url="https://example.test/flyer",
        page_count=1,
        page_image_urls=["https://example.test/page.jpg"],
        page_texts=[page_text],
        structured_offers=[_offer(page_number=1)],
    )


def test_tjek_per_offer_dates_override_publication_period():
    rows = [{
        "id": "offer-123",
        "run_from": "2026-08-23T00:00:00+02:00",
        "run_till": "2026-08-26T23:59:59+02:00",
    }]
    assert _tjek_offer_validity(rows) == {
        "offer-123": ("23.08.2026", "26.08.2026")
    }

    updated = _apply_tjek_offer_validity([_offer()], rows)[0]
    assert updated.valid_from == "23.08.2026"
    assert updated.valid_until == "26.08.2026"
    assert "provider-offer-validity" in updated.quality_signals


def test_future_offer_is_fail_closed_at_customer_api_boundary():
    offer = _offer(
        valid_from="31.12.2099",
        valid_until="02.01.2100",
        safe_to_add=True,
    )
    assert offer_is_upcoming(offer, today=date(2099, 12, 30)) is True

    payload = customer_offer_payload(offer)
    assert payload["safe_to_add"] is False
    assert payload["publication_status"] == "upcoming"

    # Legacy builds still see no actionable hotspot fields.
    assert payload["hotspot_x"] is None
    assert payload["hotspot_y"] is None
    assert payload["hotspot_width"] is None
    assert payload["hotspot_height"] is None

    # New builds can draw the marker from display-only coordinates without
    # weakening the add guard.
    assert payload["display_hotspot_x"] == 0.5
    assert payload["display_hotspot_y"] == 0.5
    assert payload["display_hotspot_width"] == 0.2
    assert payload["display_hotspot_height"] == 0.2


def test_luna_validity_schema_is_required_without_changing_existing_fields():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"visible": {"type": "boolean"}},
        "required": ["visible"],
    }
    extended = extend_fact_schema(schema)
    assert "visible" in extended["required"]
    assert "offer_valid_from" in extended["required"]
    assert "offer_valid_until" in extended["required"]
    assert "validity_confidence" in extended["required"]
    assert extended["properties"]["validity_confidence"]["maximum"] == 1


def test_luna_validity_rows_accept_safe_range_and_reject_invalid_range():
    valid = validate_page_rows([{
        "offer_valid_from": "23.08.2026",
        "offer_valid_until": "26.08.2026",
        "validity_confidence": 0.99,
    }])
    assert valid is not None
    assert safe_offer_validity(valid[0], 0.96) == (
        "23.08.2026",
        "26.08.2026",
    )
    assert starts_in_future("23.08.2026", today=date(2026, 8, 22)) is True

    assert validate_page_rows([{
        "offer_valid_from": "26.08.2026",
        "offer_valid_until": "23.08.2026",
        "validity_confidence": 0.99,
    }]) is None


def test_only_pages_with_explicit_validity_text_invalidate_old_audit_cache():
    publication = _publication(
        "Hurra! Fra søndag d. 23. august til og med onsdag d. 26. august"
    )
    assert page_has_explicit_validity_marker(publication, 1) is True
    changed = page_fingerprint(
        publication,
        1,
        publication.structured_offers,
        base_fingerprint="existing-cache-key",
    )
    assert changed != "existing-cache-key"

    ordinary = _publication("Coop bacon 10 kr. Frit valg 1 pakke.")
    assert page_has_explicit_validity_marker(ordinary, 1) is False
    unchanged = page_fingerprint(
        ordinary,
        1,
        ordinary.structured_offers,
        base_fingerprint="existing-cache-key",
    )
    assert unchanged == "existing-cache-key"

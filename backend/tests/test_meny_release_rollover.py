import json
from datetime import date

from app import flyer_publications, flyer_serving_reader
from app.meny_flyer import Offer, Publication


def _meny_publication(
    *,
    publication_id: str,
    title: str,
    week: int,
    year: int = 2026,
    valid_from: str | None = None,
    valid_until: str | None = None,
    status: str = "current",
    page_url: str | None = None,
) -> Publication:
    page_url = page_url or f"https://cdn.test/{publication_id}/Pages/1/Normal.jpg?Policy=signed&Signature=signed"
    publication = Publication(
        id=publication_id,
        retailer="MENY",
        title=title,
        week=week,
        year=year,
        valid_from=valid_from,
        valid_until=valid_until,
        status=status,
        source_url="https://ugensavis.meny.dk/",
        reader_url="https://ugensavis.meny.dk/",
        reader_kind="embedded-viewer",
        page_count=1,
        page_image_urls=[page_url],
    )
    publication.structured_offers = [Offer(
        id=f"offer-{publication_id}",
        retailer="MENY",
        publication_id=publication_id,
        publication_title=title,
        product_name="Testvare",
        price=10.0,
        source_url=publication.source_url,
        image_url=page_url,
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.2,
        hotspot_height=0.2,
        hotspot_confidence=0.95,
        quality_score=0.95,
        raw_text="Testvare 10 kr",
    )]
    return publication


def _rema_publication() -> Publication:
    return Publication(
        id="rema-current",
        retailer="REMA 1000",
        title="Uge 36",
        valid_from="31.08.2026",
        valid_until="06.09.2026",
        status="upcoming",
        source_url="https://example.test/rema",
        page_count=1,
        page_image_urls=["https://example.test/rema/page.jpg"],
    )


def test_customer_meny_week_validity_is_inferred_without_changing_source_id():
    live = _meny_publication(
        publication_id="meny-week-37",
        title="MENY uge 3726",
        week=37,
        valid_from=None,
        valid_until=None,
        status="current",
    )

    result = flyer_publications._customer_ready_publications(
        [live],
        [live],
        today=date(2026, 9, 3),
    )

    assert len(result) == 1
    publication = result[0]
    assert publication.id == "meny-week-37"
    assert publication.valid_from == "04.09.2026"
    assert publication.valid_until == "10.09.2026"
    assert publication.status == "upcoming"
    assert publication.structured_offers[0].valid_from == "04.09.2026"
    assert publication.structured_offers[0].valid_until == "10.09.2026"


def test_meny_stale_snapshot_status_is_recomputed_from_dates():
    publication = _meny_publication(
        publication_id="meny-week-36",
        title="MENY uge 3626",
        week=36,
        valid_from="28.08.2026",
        valid_until="03.09.2026",
        status="upcoming",
    )

    result = flyer_publications._customer_ready_publications(
        [publication],
        [publication],
        today=date(2026, 9, 3),
    )

    assert result[0].status == "current"


def test_non_meny_status_is_preserved_by_customer_layer():
    publication = _rema_publication()

    result = flyer_publications._customer_ready_publications(
        [publication],
        [publication],
        today=date(2026, 9, 3),
    )

    assert result == [publication]


def test_superseded_meny_release_is_never_bridged_after_live_reader_rotates():
    old = _meny_publication(
        publication_id="meny-week-36",
        title="MENY uge 3626",
        week=36,
        valid_from="28.08.2026",
        valid_until="03.09.2026",
        status="current",
    )
    live = _meny_publication(
        publication_id="meny-week-37",
        title="MENY uge 3726",
        week=37,
    )
    rema = _rema_publication()

    # Simulate Luna's stable serving overlay while the new source generation is
    # still becoming ready: it may bridge the previous verified MENY row.
    result = flyer_publications._customer_ready_publications(
        [old, rema],
        [live, rema],
        today=date(2026, 9, 3),
    )

    assert [publication.retailer for publication in result] == ["REMA 1000"]

    # Once the live release is ready, only that exact source generation survives.
    result = flyer_publications._customer_ready_publications(
        [old, live, rema],
        [live, rema],
        today=date(2026, 9, 3),
    )
    meny = [publication for publication in result if publication.retailer == "MENY"]
    assert [publication.id for publication in meny] == ["meny-week-37"]


def test_provider_failure_does_not_resurface_unverifiable_signed_meny_snapshot():
    old = _meny_publication(
        publication_id="meny-week-36",
        title="MENY uge 3626",
        week=36,
        valid_from="28.08.2026",
        valid_until="03.09.2026",
    )
    rema = _rema_publication()

    result = flyer_publications._customer_ready_publications(
        [old, rema],
        [rema],
        today=date(2026, 9, 3),
    )

    assert [publication.retailer for publication in result] == ["REMA 1000"]


def test_disk_cold_start_skips_signed_meny_but_keeps_durable_retailers(monkeypatch, tmp_path):
    path = tmp_path / "flyer-serving-cache.json"
    monkeypatch.setenv("FLYER_SERVING_CACHE_PATH", str(path))

    meny = _meny_publication(
        publication_id="meny-week-36",
        title="MENY uge 3626",
        week=36,
        valid_from="28.08.2026",
        valid_until="03.09.2026",
    )
    rema = _rema_publication()

    def row(publication: Publication) -> dict:
        return {
            "fingerprint": publication.id,
            "verified": True,
            "saved_at": 1,
            "publication": publication.model_dump(exclude={"text", "page_texts"}),
        }

    path.write_text(json.dumps({
        "version": 2,
        "publications": {
            meny.id: row(meny),
            rema.id: row(rema),
        },
    }), encoding="utf-8")

    loaded = flyer_serving_reader.load_verified_publications(today=date(2026, 9, 3))

    assert [publication.retailer for publication in loaded] == ["REMA 1000"]

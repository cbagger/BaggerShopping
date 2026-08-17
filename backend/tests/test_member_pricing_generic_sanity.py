from app import luna_semantic_audit as semantic
from app import luna_semantic_guards as guards
from app.luna_enrichment import offer_fingerprint
from app.meny_flyer import Offer, Publication


def _neophos_offer():
    return Offer(
        id="neophos-40",
        retailer="Bilka",
        publication_id="bilka-current",
        publication_title="Uge 34",
        product_name="Neophos maskinopvask",
        price=85.0,
        normal_price=None,
        unit_price="Pr. stk. max. 2,13",
        source_url="https://example.test/flyer",
        page_number=40,
        image_url="https://example.test/neophos-crop.webp",
        hotspot_x=0.60,
        hotspot_y=0.35,
        hotspot_width=0.25,
        hotspot_height=0.25,
        raw_text="Neophos maskinopvask. Frit valg 85 kr. Pr. stk. max. 2,13",
        hotspot_confidence=0.99,
        quality_score=0.99,
        quality_source="tjek-catalog",
    )


def _publication(offer):
    return Publication(
        id=offer.publication_id,
        retailer=offer.retailer,
        title=offer.publication_title,
        valid_from="14.08.2026",
        valid_until="20.08.2026",
        status="current",
        source_url=offer.source_url,
        page_count=42,
        page_image_urls=[f"https://example.test/page-{number}.webp" for number in range(1, 43)],
        structured_offers=[offer],
    )


def test_completed_bad_crop_is_reopened_under_new_sanity_contract(monkeypatch):
    offer = _neophos_offer()
    publication = _publication(offer)
    fingerprint = offer_fingerprint(offer)
    bad_facts = {
        "same_offer": True,
        "ordinary_price": 85,
        "member_price": 1.98,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus",
        "unit_price": "Pr. stk. max. 1,98 (plus); Pr. stk. max. 2,13",
        "pricing_confidence": 0.99,
    }
    store = {
        "semantic_facts": {
            semantic.offer_key(offer): {
                "source": "crop",
                "page_fingerprint": "old-page",
                "facts": {"visible": True, **bad_facts},
                "needs_crop": False,
                "crop_reasons": [],
            }
        },
        "records": {
            fingerprint: {
                "status": "completed",
                "analysis_level": "crop",
                "facts": bad_facts,
            }
        },
    }

    monkeypatch.setattr(guards, "load_store", lambda: store)

    candidates = guards._crop_candidates_allowing_build58_reverification([publication])

    assert len(candidates) == 1
    assert candidates[0].offer.id == offer.id
    assert "page-audit-member-price-is-unit-price" in candidates[0].reasons


def test_fact_schema_requires_explicit_member_price_visibility():
    schema = guards._strict_fact_schema(include_offer_id=False)

    assert schema["properties"]["membership_price_visible"] == {"type": "boolean"}
    assert "membership_price_visible" in schema["required"]

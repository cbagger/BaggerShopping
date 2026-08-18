import importlib

from app import luna_cost_policy, luna_semantic_audit, luna_semantic_engine, luna_semantic_guards
from app.meny_flyer import Offer, Publication


def _offer() -> Offer:
    return Offer(
        id="offer-1",
        retailer="Bilka",
        publication_id="pub-1",
        publication_title="Uge 34",
        product_name="Neophos maskinopvask",
        price=85,
        source_url="https://example.test",
        raw_text="PLUS PRIS 79. Frit valg 85. Pr. stk. max. 1,98",
        unit_price="Pr. stk. max. 1,98",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.1,
        hotspot_width=0.3,
        hotspot_height=0.3,
        hotspot_confidence=0.99,
    )


def _publication(offer: Offer) -> Publication:
    publication = Publication(
        id="pub-1",
        retailer="Bilka",
        title="Uge 34",
        valid_from="01.01.2099",
        valid_until="31.12.2099",
        status="current",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://example.test/page.jpg"],
    )
    publication.structured_offers = [offer]
    return publication


def test_importing_worker_does_not_install_semantic_monkeypatches():
    originals = {
        "fact_schema": luna_semantic_audit._fact_schema,
        "server_needs_crop": luna_semantic_audit._server_needs_crop,
        "crop_reasons": luna_semantic_audit._crop_reasons,
        "page_fingerprint": luna_semantic_audit.page_fingerprint,
    }

    import app.luna_worker as luna_worker
    importlib.reload(luna_worker)

    assert luna_semantic_audit._fact_schema is originals["fact_schema"]
    assert luna_semantic_audit._server_needs_crop is originals["server_needs_crop"]
    assert luna_semantic_audit._crop_reasons is originals["crop_reasons"]
    assert luna_semantic_audit.page_fingerprint is originals["page_fingerprint"]
    assert luna_worker.analyze_page_audit is luna_semantic_engine.analyze_page_audit
    assert luna_worker.analyze_crop_candidate is luna_semantic_engine.analyze_crop_candidate


def test_engine_page_schema_requires_member_visibility_for_every_target():
    offer = _offer()
    candidate = luna_semantic_engine.PageAuditCandidate(
        fingerprint="fp",
        publication=_publication(offer),
        page_number=1,
        image_url="https://example.test/page.jpg",
        offers=(offer,),
    )

    schema = luna_semantic_engine._page_schema(candidate)
    offers_schema = schema["properties"]["offers"]
    fact_schema = offers_schema["items"]

    assert offers_schema["minItems"] == 1
    assert offers_schema["maxItems"] == 1
    assert "membership_price_visible" in fact_schema["required"]
    assert fact_schema["properties"]["membership_price_visible"] == {"type": "boolean"}


def test_engine_uses_versioned_semantic_contract_fingerprint():
    offer = _offer()
    publication = _publication(offer)

    base = luna_semantic_audit.page_fingerprint(publication, 1, [offer])
    engine = luna_semantic_engine.page_fingerprint(publication, 1, [offer])

    assert engine != base
    assert engine == luna_semantic_guards._versioned_page_fingerprint(publication, 1, [offer])


def test_neophos_unit_price_conflict_still_requires_targeted_crop():
    offer = _offer()
    facts = {
        "visible": True,
        "membership_price_visible": True,
        "ordinary_price": 85,
        "member_price": 1.98,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus appen",
        "requires_activation": False,
        "before_price": None,
        "unit_price": "Pr. stk. max. 1,98",
        "package_size": "40-53 stk",
        "multiple_products": False,
        "variants": [],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.99,
        "needs_crop_verification": False,
    }

    assert luna_cost_policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    reasons = luna_cost_policy._balanced_crop_reasons(offer, facts, True)
    assert "page-audit-member-price-is-unit-price" in reasons

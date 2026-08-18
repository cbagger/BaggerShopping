import asyncio
import json

import httpx

from app import luna_enrichment as luna
from app import luna_cost_policy as cost_policy
from app import luna_semantic_audit as semantic
from app import luna_semantic_engine as engine
from app.meny_flyer import Offer, Publication


def _isolated(monkeypatch, tmp_path, *, enabled=True):
    config = tmp_path / "luna-config.json"
    store = tmp_path / "luna-store.json"
    config.write_text(json.dumps({
        "enabled": enabled,
        "apply_results": True,
        "monthly_budget_dkk": 25,
        "max_requests_per_month": 250,
        "max_requests_per_scan": 20,
        "min_apply_confidence": 0.96,
    }), encoding="utf-8")
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)
    return config


def _offer(*, offer_id="becel", name="Becel flydende", price=15, variants=None, variant_confidence=0.62):
    return Offer(
        id=offer_id,
        retailer="Bilka",
        publication_id="bilka-week-34",
        publication_title="Bilka uge 34",
        product_name=name,
        price=price,
        source_url="https://example.test",
        page_number=9,
        hotspot_x=0.55,
        hotspot_y=0.50,
        hotspot_width=0.35,
        hotspot_height=0.40,
        raw_text=f"{name} 500 ml",
        variants=variants or [],
        quality_score=0.9,
        variant_confidence=variant_confidence,
    )


def _publication(offer):
    return Publication(
        id=offer.publication_id,
        retailer=offer.retailer,
        title=offer.publication_title,
        source_url="https://example.test",
        page_count=9,
        page_image_urls=[
            *[f"https://images.test/page-{index}.jpg" for index in range(1, 9)],
            "https://images.test/page-9.jpg?token=rotates",
        ],
        structured_offers=[offer],
    )


def test_page_fingerprint_ignores_rotating_image_query():
    offer = _offer()
    first = _publication(offer)
    second = first.model_copy(update={
        "page_image_urls": [*first.page_image_urls[:-1], "https://images.test/page-9.jpg?token=new"]
    })
    assert semantic.page_fingerprint(first, 9, [offer]) == semantic.page_fingerprint(second, 9, [offer])


def test_page_audit_becel_visual_only_member_price_requires_verification_crop():
    offer = _offer()
    facts = {
        "visible": True,
        "membership_price_visible": True,
        "product_name": "Becel flydende",
        "brand": "Becel",
        "ordinary_price": 15,
        "member_price": 12,
        "member_program": "Bilka Plus",
        "member_app": "Bilka Plus",
        "requires_activation": False,
        "before_price": None,
        "unit_price": "24 kr/l",
        "package_size": "500 ml",
        "multiple_products": False,
        "variants": [],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.80,
        "needs_crop_verification": False,
    }
    assert cost_policy._balanced_server_needs_crop(offer, facts, 0.96) is True
    assert "page-audit-new-member-price-verification" in cost_policy._balanced_crop_reasons(
        offer, facts, True
    )


def test_multiple_products_without_named_variants_requires_crop():
    offer = _offer(name="PÅLÆGSSLAGTEREN Pålæg")
    facts = {
        "visible": True,
        "product_name": offer.product_name,
        "brand": None,
        "ordinary_price": None,
        "member_price": 10,
        "member_program": "Lidl Plus",
        "member_app": "Lidl Plus",
        "requires_activation": False,
        "before_price": None,
        "unit_price": None,
        "package_size": "70-150 g",
        "multiple_products": True,
        "variants": [],
        "identity_confidence": 0.98,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.70,
        "needs_crop_verification": False,
    }
    assert semantic._server_needs_crop(offer, facts, 0.96) is True


def test_page_audit_stages_visual_only_member_price_until_crop(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    offer = _offer()
    publication = _publication(offer)
    candidate = engine.collect_page_audit_candidates([publication])[0]

    page_result = {
        "offers": [{
            "offer_id": offer.id,
            "visible": True,
            "membership_price_visible": True,
            "product_name": "Becel flydende",
            "brand": "Becel",
            "ordinary_price": 15,
            "member_price": 12,
            "member_program": "Bilka Plus",
            "member_app": "Bilka Plus",
            "requires_activation": False,
            "before_price": None,
            "unit_price": "24 kr/l",
            "package_size": "500 ml",
            "multiple_products": False,
            "variants": [],
            "identity_confidence": 0.99,
            "pricing_confidence": 0.99,
            "variant_confidence": 0.80,
            "needs_crop_verification": False,
        }]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "resp_page",
            "model": "gpt-5.6-luna",
            "usage": {"input_tokens": 1500, "output_tokens": 250},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(page_result)}
            ]}],
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await engine.analyze_page_audit(candidate, client=client)

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["crop_needed"] == 1

    store = luna.load_store()
    row = store["semantic_facts"][engine.offer_key(offer)]
    assert row["source"] == "page-audit"
    assert row["needs_crop"] is True
    assert "page-audit-new-member-price-verification" in row["crop_reasons"]
    assert row["facts"]["ordinary_price"] == 15
    assert row["facts"]["member_price"] == 12

    signature = luna.offer_pricing_signature(offer)
    assert signature not in store["pricing_index"]
    assert store["usage"][luna.month_key()]["by_kind"]["page-audit"]["requests"] == 1


def test_master_off_hides_semantic_facts_without_deleting_cache(monkeypatch, tmp_path):
    config = _isolated(monkeypatch, tmp_path, enabled=True)
    offer = _offer()
    luna.save_store({
        "records": {},
        "pricing_index": {},
        "usage": {},
        "events": [],
        "semantic_facts": {
            semantic.offer_key(offer): {
                "source": "page-audit",
                "facts": {
                    "visible": True,
                    "ordinary_price": 15,
                    "member_price": 12,
                    "multiple_products": False,
                    "variants": [],
                    "identity_confidence": 0.99,
                    "pricing_confidence": 0.99,
                    "variant_confidence": 0.8,
                },
            }
        },
    })
    assert semantic.semantic_facts_for_offer(offer) is not None

    config.write_text(json.dumps({"enabled": False, "apply_results": True}), encoding="utf-8")
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    assert semantic.semantic_facts_for_offer(offer) is None
    assert semantic.offer_key(offer) in luna.load_store()["semantic_facts"]

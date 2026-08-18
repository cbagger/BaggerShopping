import asyncio
import json

import httpx

from app import luna_enrichment as luna
from app.member_pricing import detect_member_pricing
from app.meny_flyer import Offer, Publication


def _isolated_luna(monkeypatch, tmp_path, *, enabled=False, apply_results=True):
    config = tmp_path / "luna-config.json"
    store = tmp_path / "luna-store.json"
    config.write_text(json.dumps({
        "enabled": enabled,
        "apply_results": apply_results,
        "monthly_budget_dkk": 25,
        "max_requests_per_month": 250,
        "min_apply_confidence": 0.96,
    }), encoding="utf-8")
    monkeypatch.setattr(luna, "CONFIG_PATH", config)
    monkeypatch.setattr(luna, "STORE_PATH", store)
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    monkeypatch.setattr(luna, "_store_cache", None)
    monkeypatch.setattr(luna, "_store_signature", None)
    return config, store


def _offer(raw_text: str, *, price=15, normal_price=None, retailer="MENY"):
    return Offer(
        id="offer-1",
        retailer=retailer,
        publication_id="publication-1",
        publication_title="Uge 34",
        product_name="Testvare",
        price=price,
        normal_price=normal_price,
        source_url="https://example.test",
        page_number=1,
        hotspot_x=0.1,
        hotspot_y=0.2,
        hotspot_width=0.3,
        hotspot_height=0.2,
        raw_text=raw_text,
        quality_score=0.9,
        variant_confidence=0.9,
    )


def test_luna_is_disabled_by_default_and_never_needed_for_normal_offer(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=False)
    offer = _offer("Testvare 15 kr.")
    assert luna.member_pricing_override(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=f"{offer.product_name} {offer.raw_text}",
        unit_price=offer.unit_price,
    ) is None
    decision = luna.review_decision(offer)
    assert decision.review is False
    assert luna.status_payload()["enabled"] is False


def test_ai_gate_selects_uncertain_page_member_price(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=False)
    offer = _offer(
        "[kurv-page-context] Testvare 15,- MEDLEMSPRIS 8,95 [/kurv-page-context]"
    )
    decision = luna.review_decision(offer)
    assert decision.review is True
    assert decision.priority >= 80
    assert "member-signal-without-safe-price" in decision.reasons
    assert "pricing" in decision.requested_fields


def test_high_confidence_cached_luna_result_can_correct_member_price(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=True)
    text = "Testvare [kurv-page-context] PLUS PRIS 10,- Normal pris 14,- [/kurv-page-context]"
    signature = luna.pricing_signature(
        retailer="føtex", price=14, normal_price=None, text=text, unit_price=None
    )
    fingerprint = "abc123"
    luna.save_store({
        "records": {fingerprint: {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "ordinary_price": 14,
                "member_price": 10,
                "member_program": "føtex Plus",
                "member_app": "føtex Plus",
                "requires_activation": False,
                "pricing_confidence": 0.99,
            },
        }},
        "pricing_index": {signature: fingerprint},
        "usage": {},
        "events": [],
    })
    override = luna.member_pricing_override(
        retailer="føtex", price=14, normal_price=None, text=text, unit_price=None
    )
    assert override is not None
    assert override["ordinary_price"] == 14
    assert override["member_price"] == 10
    assert override["member_program"] == "føtex Plus"

    pricing = detect_member_pricing(
        retailer="føtex", price=14, normal_price=None, text=text
    )
    assert pricing is not None
    assert pricing.source == "luna-verified"
    assert pricing.ordinary_price == 14
    assert pricing.member_price == 10


def test_luna_can_authoritatively_suppress_false_member_badge(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=True)
    text = "GM juice 16 kr. [kurv-page-context] nabo MEDLEMSPRIS 9,95 [/kurv-page-context]"
    signature = luna.pricing_signature(
        retailer="MENY", price=16, normal_price=None, text=text, unit_price=None
    )
    luna.save_store({
        "records": {"no-member": {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "ordinary_price": 16,
                "member_price": None,
                "member_program": None,
                "member_app": None,
                "requires_activation": False,
                "pricing_confidence": 0.99,
            },
        }},
        "pricing_index": {signature: "no-member"},
        "usage": {},
        "events": [],
    })
    assert detect_member_pricing(
        retailer="MENY", price=16, normal_price=None, text=text
    ) is None


def test_turning_luna_off_immediately_restores_deterministic_path(monkeypatch, tmp_path):
    config_path, _ = _isolated_luna(monkeypatch, tmp_path, enabled=True)
    text = "Testvare [kurv-page-context] PLUS PRIS 10,- 14,- [/kurv-page-context]"
    signature = luna.pricing_signature(
        retailer="føtex", price=14, normal_price=None, text=text, unit_price=None
    )
    luna.save_store({
        "records": {"verified": {
            "status": "completed",
            "facts": {
                "same_offer": True,
                "ordinary_price": 14,
                "member_price": 10,
                "member_program": "føtex Plus",
                "member_app": "føtex Plus",
                "requires_activation": False,
                "pricing_confidence": 0.99,
            },
        }},
        "pricing_index": {signature: "verified"},
        "usage": {},
        "events": [],
    })
    assert luna.member_pricing_override(
        retailer="føtex", price=14, normal_price=None, text=text, unit_price=None
    ) is not None

    config_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    assert luna.member_pricing_override(
        retailer="føtex", price=14, normal_price=None, text=text, unit_price=None
    ) is None
    # The cached correction remains stored; OFF only changes which layer Kurv reads.
    assert luna.load_store()["records"]["verified"]["status"] == "completed"


def test_budget_guard_stops_new_requests(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=True)
    month = luna.month_key()
    luna.save_store({
        "records": {}, "pricing_index": {}, "events": [],
        "usage": {month: {"requests": 1, "estimated_cost_dkk": 25.0}},
    })
    assert luna.budget_allows_request() is False


def test_legacy_2000_request_default_migrates_to_emergency_cap(monkeypatch, tmp_path):
    config_path, _ = _isolated_luna(monkeypatch, tmp_path, enabled=True)
    config_path.write_text(json.dumps({
        "enabled": True,
        "apply_results": True,
        "monthly_budget_dkk": 20.0,
        "max_requests_per_month": 2000,
    }), encoding="utf-8")
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)

    config = luna.load_config()
    assert config["config_version"] == luna.CONFIG_VERSION
    assert config["max_requests_per_month"] == luna.EMERGENCY_REQUEST_LIMIT

    month = luna.month_key()
    luna.save_store({
        "records": {}, "pricing_index": {}, "events": [],
        "usage": {month: {"requests": 2000, "estimated_cost_dkk": 14.3}},
    })
    assert luna.budget_allows_request(config) is True


def test_custom_request_limit_remains_authoritative(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=True)
    config = luna.load_config()
    assert config["config_version"] == luna.CONFIG_VERSION
    assert config["max_requests_per_month"] == 250

    month = luna.month_key()
    luna.save_store({
        "records": {}, "pricing_index": {}, "events": [],
        "usage": {month: {"requests": 250, "estimated_cost_dkk": 1.0}},
    })
    assert luna.budget_allows_request(config) is False


def test_emergency_request_cap_still_stops_runaway_usage(monkeypatch, tmp_path):
    config_path, _ = _isolated_luna(monkeypatch, tmp_path, enabled=True)
    config_path.write_text(json.dumps({
        "config_version": luna.CONFIG_VERSION,
        "enabled": True,
        "monthly_budget_dkk": 20.0,
        "max_requests_per_month": luna.EMERGENCY_REQUEST_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(luna, "_config_cache", None)
    monkeypatch.setattr(luna, "_config_signature", None)
    config = luna.load_config()

    month = luna.month_key()
    luna.save_store({
        "records": {}, "pricing_index": {}, "events": [],
        "usage": {month: {
            "requests": luna.EMERGENCY_REQUEST_LIMIT,
            "estimated_cost_dkk": 1.0,
        }},
    })
    assert luna.budget_allows_request(config) is False


def test_mocked_response_is_cached_once_with_usage_and_pricing_index(monkeypatch, tmp_path):
    _isolated_luna(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    publication = Publication(
        id="publication-1",
        retailer="MENY",
        title="Uge 34",
        source_url="https://example.test",
        page_count=1,
        page_image_urls=["https://images.test/page.jpg"],
    )
    offer = _offer(
        "[kurv-page-context] Testvare 15,- MEDLEMSPRIS 8,95 [/kurv-page-context]"
    )
    decision = luna.review_decision(offer)
    candidate = luna.Candidate(luna.offer_fingerprint(offer), publication, offer, decision)
    facts = {
        "same_offer": True,
        "product_name": "Testvare",
        "brand": None,
        "ordinary_price": 15,
        "member_price": 8.95,
        "member_program": "MENY medlemspris",
        "member_app": "MENY-appen",
        "requires_activation": True,
        "before_price": None,
        "unit_price": None,
        "variants": [],
        "identity_confidence": 0.99,
        "pricing_confidence": 0.99,
        "variant_confidence": 0.5,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.openai.com/v1/responses")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={
            "id": "resp_test",
            "model": "gpt-5.6-luna",
            "usage": {"input_tokens": 1000, "output_tokens": 100},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(facts)}
            ]}],
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await luna.analyze_candidate(candidate, client=client)

    result = asyncio.run(run())
    assert result["status"] == "completed"
    store = luna.load_store()
    assert store["pricing_index"][luna.offer_pricing_signature(offer)] == candidate.fingerprint
    status = luna.usage_status()
    assert status["requests"] == 1
    assert status["input_tokens"] == 1000
    assert status["output_tokens"] == 100
    assert status["estimated_cost_dkk"] > 0

    # Fingerprinting/cache makes the same source offer ineligible for a second call.
    publication.structured_offers = [offer]
    assert luna.collect_candidates([publication]) == []

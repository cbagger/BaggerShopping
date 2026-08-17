from app import luna_enrichment
from app import luna_pricing_fastpath


def _pricing_args():
    return {
        "retailer": "MENY",
        "price": 25.0,
        "normal_price": 32.0,
        "text": "Kaffe medlemspris 25,00 normalpris 32,00",
        "unit_price": None,
    }


def test_fast_pricing_lookup_never_calls_cloning_load_store(monkeypatch):
    args = _pricing_args()
    signature = luna_enrichment.pricing_signature(**args)

    monkeypatch.setattr(
        luna_enrichment,
        "load_config",
        lambda: {
            "enabled": True,
            "apply_results": True,
            "min_apply_confidence": 0.96,
        },
    )
    monkeypatch.setattr(
        luna_enrichment,
        "load_store",
        lambda: (_ for _ in ()).throw(
            AssertionError("mobile pricing lookup must not clone the complete Luna store")
        ),
    )
    monkeypatch.setattr(luna_enrichment, "_signature", lambda _: None)

    luna_enrichment._store_cache = {
        "records": {
            "offer-fingerprint": {
                "status": "completed",
                "facts": {
                    "same_offer": True,
                    "ordinary_price": 32.0,
                    "member_price": 25.0,
                    "member_program": "MENY medlemspris",
                    "member_app": "MENY-appen",
                    "requires_activation": False,
                    "pricing_confidence": 0.99,
                },
            },
        },
        "pricing_index": {signature: "offer-fingerprint"},
        "usage": {},
        "events": [],
    }
    luna_enrichment._store_signature = None

    result = luna_pricing_fastpath.member_pricing_override_fast(**args)

    assert result == {
        "authoritative": True,
        "ordinary_price": 32.0,
        "member_price": 25.0,
        "member_program": "MENY medlemspris",
        "member_app": "MENY-appen",
        "requires_activation": False,
        "pricing_confidence": 0.99,
        "fingerprint": "offer-fingerprint",
    }


def test_fast_pricing_lookup_returns_none_for_unverified_record(monkeypatch):
    args = _pricing_args()
    signature = luna_enrichment.pricing_signature(**args)

    monkeypatch.setattr(
        luna_enrichment,
        "load_config",
        lambda: {
            "enabled": True,
            "apply_results": True,
            "min_apply_confidence": 0.96,
        },
    )
    monkeypatch.setattr(luna_enrichment, "_signature", lambda _: None)

    luna_enrichment._store_cache = {
        "records": {
            "offer-fingerprint": {
                "status": "completed",
                "facts": {
                    "same_offer": True,
                    "member_price": 25.0,
                    "pricing_confidence": 0.50,
                },
            },
        },
        "pricing_index": {signature: "offer-fingerprint"},
        "usage": {},
        "events": [],
    }
    luna_enrichment._store_signature = None

    assert luna_pricing_fastpath.member_pricing_override_fast(**args) is None

import json

from app import luna_enrichment
from app import luna_pricing_fastpath
from app.luna_pricing_reader import LunaPricingReader


def _pricing_args():
    return {
        "retailer": "MENY",
        "price": 25.0,
        "normal_price": 32.0,
        "text": "Kaffe medlemspris 25,00 normalpris 32,00",
        "unit_price": None,
    }


def _store(signature: str, *, confidence: float = 0.99, member_price: float = 25.0):
    return {
        "records": {
            "offer-fingerprint": {
                "status": "completed",
                "facts": {
                    "same_offer": True,
                    "ordinary_price": 32.0,
                    "member_price": member_price,
                    "member_program": "MENY medlemspris",
                    "member_app": "MENY-appen",
                    "requires_activation": False,
                    "pricing_confidence": confidence,
                },
            },
        },
        "pricing_index": {signature: "offer-fingerprint"},
        "usage": {},
        "events": [],
    }


def test_public_pricing_reader_never_calls_cloning_load_store(monkeypatch, tmp_path):
    args = _pricing_args()
    signature = luna_enrichment.pricing_signature(**args)
    store_path = tmp_path / "luna.json"
    store_path.write_text(json.dumps(_store(signature)), encoding="utf-8")

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
            AssertionError("customer pricing must not clone the complete Luna store")
        ),
    )

    reader = LunaPricingReader(store_path)
    result = reader.member_pricing_override(**args)

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


def test_public_pricing_reader_refreshes_when_worker_store_changes(monkeypatch, tmp_path):
    args = _pricing_args()
    signature = luna_enrichment.pricing_signature(**args)
    store_path = tmp_path / "luna.json"
    store_path.write_text(json.dumps(_store(signature, member_price=25.0)), encoding="utf-8")

    monkeypatch.setattr(
        luna_enrichment,
        "load_config",
        lambda: {
            "enabled": True,
            "apply_results": True,
            "min_apply_confidence": 0.96,
        },
    )

    reader = LunaPricingReader(store_path)
    assert reader.member_pricing_override(**args)["member_price"] == 25.0

    updated = _store(signature, member_price=24.0)
    updated["events"] = [{"changed": True}]
    store_path.write_text(json.dumps(updated), encoding="utf-8")

    assert reader.member_pricing_override(**args)["member_price"] == 24.0


def test_public_pricing_reader_rejects_unverified_record(monkeypatch, tmp_path):
    args = _pricing_args()
    signature = luna_enrichment.pricing_signature(**args)
    store_path = tmp_path / "luna.json"
    store_path.write_text(json.dumps(_store(signature, confidence=0.50)), encoding="utf-8")

    monkeypatch.setattr(
        luna_enrichment,
        "load_config",
        lambda: {
            "enabled": True,
            "apply_results": True,
            "min_apply_confidence": 0.96,
        },
    )

    assert LunaPricingReader(store_path).member_pricing_override(**args) is None


def test_compatibility_fastpath_delegates_to_public_reader(monkeypatch):
    sentinel = object()

    monkeypatch.setattr(
        "app.luna_pricing_fastpath.member_pricing_override_fast",
        lambda **_: sentinel,
    )

    luna_pricing_fastpath.install()

    assert luna_enrichment.member_pricing_override(**_pricing_args()) is sentinel

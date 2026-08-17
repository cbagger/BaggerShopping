from __future__ import annotations

from typing import Any

from . import luna_enrichment


def _cached_store_reference() -> dict[str, Any]:
    """Return the process-local Luna store without cloning the whole database.

    ``luna_enrichment.load_store`` deliberately returns a deep JSON copy because
    worker/control callers may mutate the returned structure before saving it.
    A Mobile API membership-price lookup is read-only and can happen hundreds
    of times while one flyer is opened, so cloning the complete multi-megabyte
    store for every offer is both unnecessary and expensive.

    The shared lock still protects replacement of the cache. The returned
    object is never mutated by this module.
    """
    disk_signature = luna_enrichment._signature(luna_enrichment.STORE_PATH)

    with luna_enrichment._store_lock:
        if (
            luna_enrichment._store_cache is None
            or luna_enrichment._store_signature != disk_signature
        ):
            value = luna_enrichment._read_json(
                luna_enrichment.STORE_PATH,
                luna_enrichment._empty_store(),
            )
            for key, default in luna_enrichment._empty_store().items():
                value.setdefault(key, default)
            luna_enrichment._store_cache = value
            luna_enrichment._store_signature = disk_signature

        return luna_enrichment._store_cache


def member_pricing_override_fast(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None,
) -> dict[str, Any] | None:
    """Read one cached Luna pricing record without copying the complete store."""
    config = luna_enrichment.load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return None

    signature = luna_enrichment.pricing_signature(
        retailer=retailer,
        price=price,
        normal_price=normal_price,
        text=text,
        unit_price=unit_price,
    )

    store = _cached_store_reference()
    with luna_enrichment._store_lock:
        fingerprint = store.get("pricing_index", {}).get(signature)
        row = store.get("records", {}).get(fingerprint) if fingerprint else None
        if not isinstance(row, dict) or row.get("status") != "completed":
            return None
        facts = row.get("facts")
        if not isinstance(facts, dict) or not facts.get("same_offer"):
            return None

        confidence = float(facts.get("pricing_confidence") or 0)
        if confidence < float(config.get("min_apply_confidence", 0.96)):
            return None

        # Copy only the scalar fields needed by the caller. Never expose the
        # mutable cached record itself.
        return {
            "authoritative": True,
            "ordinary_price": facts.get("ordinary_price"),
            "member_price": facts.get("member_price"),
            "member_program": facts.get("member_program"),
            "member_app": facts.get("member_app"),
            "requires_activation": bool(facts.get("requires_activation")),
            "pricing_confidence": confidence,
            "fingerprint": fingerprint,
        }


def install() -> None:
    """Install the read-only lookup only in the Mobile API process."""
    luna_enrichment.member_pricing_override = member_pricing_override_fast

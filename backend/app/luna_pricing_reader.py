from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import luna_enrichment


class LunaPricingReader:
    """Read verified Luna pricing facts without exposing the mutable worker store.

    The Luna worker owns writes to ``luna-enrichment-store.json``. Customer-facing
    API requests only need a tiny immutable projection from that store. Keeping
    a process-local read cache here avoids cloning the multi-megabyte worker store
    for every offer and, unlike the old fastpath, does not depend on any private
    ``luna_enrichment._store_*`` implementation details.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        self.store_path = store_path or luna_enrichment.STORE_PATH
        self._lock = threading.RLock()
        self._signature: tuple[int, int] | None = None
        self._store: dict[str, Any] | None = None

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.store_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _load_reference(self) -> dict[str, Any]:
        signature = self._file_signature()
        with self._lock:
            if self._store is not None and signature == self._signature:
                return self._store

            try:
                value = json.loads(self.store_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = {}

            if not isinstance(value, dict):
                value = {}
            value.setdefault("records", {})
            value.setdefault("pricing_index", {})

            self._store = value
            self._signature = signature
            return value

    def member_pricing_override(
        self,
        *,
        retailer: str,
        price: float | None,
        normal_price: float | None,
        text: str,
        unit_price: str | None,
    ) -> dict[str, Any] | None:
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

        store = self._load_reference()
        with self._lock:
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


_default_reader = LunaPricingReader()


def member_pricing_override(
    *,
    retailer: str,
    price: float | None,
    normal_price: float | None,
    text: str,
    unit_price: str | None,
) -> dict[str, Any] | None:
    """Stable public read API used by customer-facing pricing paths."""

    return _default_reader.member_pricing_override(
        retailer=retailer,
        price=price,
        normal_price=normal_price,
        text=text,
        unit_price=unit_price,
    )


__all__ = ["LunaPricingReader", "member_pricing_override"]

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from .flyer_readiness import (
    load_store as load_readiness_store,
    publication_fingerprint,
    publication_is_ready,
)
from .luna_enrichment import load_config, load_store, offer_fingerprint
from .luna_offer_validity import safe_offer_validity
from .luna_semantic_audit import offer_key
from .meny_flyer import Offer, OfferVariant, Publication
from .retailer_sources import is_active_retailer


_SIZE_ONLY_VARIANT_RE = re.compile(
    r"^\s*(?:ca\.?\s*)?(?:"
    r"\d+(?:[.,]\d+)?\s*(?:[-–]\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:g|kg|ml|cl|dl|l|stk\.?|styk(?:ker)?|pk\.?|pakker?)"
    r"|\d+\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:g|kg|ml|cl|dl|l|stk\.?)"
    r")\s*$",
    re.IGNORECASE,
)
_GENERIC_VARIANT_RE = re.compile(
    r"^\s*(?:flere\s+varianter|frit\s+valg|assorterede?|diverse|flere\s+slags)\s*$",
    re.IGNORECASE,
)
_SERVING_CACHE_VERSION = 2
# Source readiness and deterministic presentation are deliberately versioned
# separately. A parser/member-pricing improvement must refresh the verified
# serving snapshot without pretending that the retailer published a new flyer
# and without creating Luna work.
_SERVING_CACHE_CONTENT_REVISION = 3
_LUNA_PICKER_VARIANT_CONFIDENCE = 0.80
_LEGACY_LUNA_VARIANT_CONFIDENCE = 0.99


def _serving_cache_path() -> Path:
    return Path(os.getenv("FLYER_SERVING_CACHE_PATH", "/data/flyer-serving-cache.json"))


def _empty_serving_cache(*, migrated_from: object = None) -> dict:
    value = {"version": _SERVING_CACHE_VERSION, "publications": {}}
    if migrated_from is not None:
        value["migrated_from_version"] = migrated_from
    return value


def _load_serving_cache() -> dict:
    try:
        value = json.loads(_serving_cache_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_serving_cache()
    if not isinstance(value, dict):
        return _empty_serving_cache()

    version = value.get("version")
    if version != _SERVING_CACHE_VERSION:
        return _empty_serving_cache(migrated_from=version)

    value.setdefault("publications", {})
    return value


def _save_serving_cache(store: dict) -> None:
    path = _serving_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        store["version"] = _SERVING_CACHE_VERSION
        store["updated_at"] = int(time.time())
        temporary.write_text(
            json.dumps(store, ensure_ascii=False, separators=(",", ":")),
            "utf-8",
        )
        temporary.replace(path)
    except OSError:
        return


def _raw_offer_payload(offer: Offer) -> dict:
    return BaseModel.model_dump(offer)


def _publication_snapshot(publication: Publication, *, verified: bool) -> dict:
    payload = publication.model_dump(exclude={"text", "page_texts"})
    payload["structured_offers"] = [
        _raw_offer_payload(offer) for offer in publication.structured_offers
    ]
    return {
        "fingerprint": publication_fingerprint(publication),
        "verified": verified,
        "content_revision": _SERVING_CACHE_CONTENT_REVISION,
        "saved_at": int(time.time()),
        "publication": payload,
    }


def _restore_publication(row: object) -> Publication | None:
    if not isinstance(row, dict):
        return None
    payload = row.get("publication")
    if not isinstance(payload, dict):
        return None
    try:
        return Publication.model_validate(payload)
    except Exception:
        return None


def _publication_not_expired(publication: Publication) -> bool:
    if publication.status == "expired":
        return False
    if not publication.valid_until:
        return True
    try:
        return datetime.strptime(publication.valid_until, "%d.%m.%Y").date() >= date.today()
    except ValueError:
        return True


def _serve_stable_publications(publications: list[Publication]) -> list[Publication]:
    publications = [
        publication for publication in publications
        if is_active_retailer(publication.retailer)
    ]
    readiness = load_readiness_store()
    if not readiness.get("initialized"):
        return publications

    store = _load_serving_cache()
    rows = store.setdefault("publications", {})
    result: list[Publication] = []
    current_ids: set[str] = set()
    changed = bool(store.get("migrated_from_version") is not None)

    for publication in publications:
        current_ids.add(publication.id)
        row = rows.get(publication.id)
        cached = _restore_publication(row)
        fingerprint = publication_fingerprint(publication)

        if publication_is_ready(publication):
            result.append(publication)
            if (
                not isinstance(row, dict)
                or row.get("fingerprint") != fingerprint
                or not row.get("verified")
                or row.get("content_revision") != _SERVING_CACHE_CONTENT_REVISION
            ):
                rows[publication.id] = _publication_snapshot(publication, verified=True)
                changed = True
            continue

        # Only a previously verified generation may bridge a processing window.
        # A brand-new, unverified flyer must never leak raw provider pricing to
        # customers while Luna is still establishing ordinary/member roles.
        if (
            isinstance(row, dict)
            and row.get("verified") is True
            and cached is not None
            and _publication_not_expired(cached)
        ):
            result.append(cached)
            continue

        # Keep a provisional diagnostic snapshot, but deliberately do not serve
        # it until readiness marks this exact source fingerprint as verified.
        rows[publication.id] = _publication_snapshot(publication, verified=False)
        changed = True

    retired_ids: list[str] = []
    for publication_id, row in rows.items():
        if publication_id in current_ids or not isinstance(row, dict) or not row.get("verified"):
            continue
        cached = _restore_publication(row)
        if cached is not None and not is_active_retailer(cached.retailer):
            retired_ids.append(publication_id)
            changed = True
            continue
        if cached is not None and _publication_not_expired(cached):
            result.append(cached)

    for publication_id in retired_ids:
        rows.pop(publication_id, None)

    if changed:
        store.pop("migrated_from_version", None)
        _save_serving_cache(store)
    return result


def _safe_luna_variant_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name:
        return None
    if _SIZE_ONLY_VARIANT_RE.fullmatch(name) or _GENERIC_VARIANT_RE.fullmatch(name):
        return None
    return name


def _record_facts(offer: Offer, records: dict) -> dict | None:
    row = records.get(offer_fingerprint(offer))
    facts = row.get("facts") if isinstance(row, dict) and row.get("status") == "completed" else None
    if not isinstance(facts, dict) or not facts.get("same_offer"):
        return None
    return facts


def _may_surface_picker_variants(*, semantic: dict | None, facts: dict, confidence: float) -> bool:
    if semantic is not None:
        return facts.get("multiple_products") is True and confidence >= _LUNA_PICKER_VARIANT_CONFIDENCE
    return confidence >= _LEGACY_LUNA_VARIANT_CONFIDENCE


def apply_cached_enrichment(publications: list[Publication]) -> list[Publication]:
    publications = _serve_stable_publications(publications)

    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return publications

    store = load_store()
    records = store.get("records", {})
    semantic_rows = store.get("semantic_facts", {})
    threshold = float(config.get("min_apply_confidence", 0.96))
    result: list[Publication] = []

    for publication in publications:
        changed = False
        offers: list[Offer] = []
        for offer in publication.structured_offers:
            semantic_row = semantic_rows.get(offer_key(offer))
            semantic = None
            if isinstance(semantic_row, dict):
                candidate = semantic_row.get("facts")
                if isinstance(candidate, dict) and candidate.get("visible"):
                    semantic = candidate
            semantic_needs_crop = bool(
                isinstance(semantic_row, dict) and semantic_row.get("needs_crop")
            )
            legacy = _record_facts(offer, records)
            facts = semantic or legacy
            if not isinstance(facts, dict):
                offers.append(offer)
                continue

            updates: dict = {}
            identity_confidence = float(facts.get("identity_confidence") or 0)
            pricing_confidence = float(facts.get("pricing_confidence") or 0)
            variant_confidence = float(facts.get("variant_confidence") or 0)
            signals = list(offer.quality_signals)

            if semantic is not None:
                signals.append("luna-semantic-audited")
                if facts.get("multiple_products"):
                    signals.append("luna-multiple-products")
                if facts.get("package_size"):
                    signals.append("luna-package-size-known")

                valid_from, valid_until = safe_offer_validity(facts, threshold)
                if valid_from:
                    updates["valid_from"] = valid_from
                    signals.append("luna-offer-validity")
                if valid_until:
                    updates["valid_until"] = valid_until
                    signals.append("luna-offer-validity")
                # A future start date is planning metadata, not a safety error.
                # Preserve the offer's existing safe_to_add value so genuinely
                # unsafe parsing remains blocked while upcoming offers can still
                # be added and carry their start date to the shopping-list badge.

            if (
                not semantic_needs_crop
                and not offer.brand
                and facts.get("brand")
                and identity_confidence >= threshold
            ):
                updates["brand"] = str(facts["brand"]).strip()

            if (
                semantic is not None
                and not semantic_needs_crop
                and facts.get("member_price") is None
                and isinstance(facts.get("ordinary_price"), (int, float))
                and pricing_confidence >= 0.99
            ):
                updates["price"] = round(float(facts["ordinary_price"]), 2)

            if (
                not semantic_needs_crop
                and _may_surface_picker_variants(
                    semantic=semantic,
                    facts=facts,
                    confidence=variant_confidence,
                )
                and offer.variant_confidence < 0.90
            ):
                names = [
                    name
                    for value in facts.get("variants", [])
                    if (name := _safe_luna_variant_name(value)) is not None
                ]
                names = list(dict.fromkeys(names))[:12]
                if len(names) >= 2:
                    updates["variants"] = [
                        OfferVariant(
                            id=hashlib.sha256(
                                f"{offer.id}|luna|{name}".encode()
                            ).hexdigest()[:20],
                            name=name,
                        )
                        for name in names
                    ]
                    updates["variant_confidence"] = variant_confidence
                    signals.append("luna-picker-variants")
                    if variant_confidence >= _LEGACY_LUNA_VARIANT_CONFIDENCE:
                        signals.append("luna-verified-variants")

            if signals != offer.quality_signals:
                updates["quality_signals"] = list(dict.fromkeys(signals))

            if updates:
                offers.append(offer.model_copy(update=updates))
                changed = True
            else:
                offers.append(offer)

        result.append(
            publication.model_copy(update={"structured_offers": offers}, deep=True)
            if changed else publication
        )
    return result

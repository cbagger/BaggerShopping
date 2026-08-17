from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from .flyer_readiness import (
    load_store as load_readiness_store,
    publication_fingerprint,
    publication_is_ready,
)
from .luna_enrichment import load_config, load_store, offer_fingerprint
from .luna_semantic_audit import offer_key
from .meny_flyer import Offer, OfferVariant, Publication


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
_SERVING_CACHE_VERSION = 1


def _serving_cache_path() -> Path:
    return Path(os.getenv("FLYER_SERVING_CACHE_PATH", "/data/flyer-serving-cache.json"))


def _load_serving_cache() -> dict:
    try:
        value = json.loads(_serving_cache_path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _SERVING_CACHE_VERSION, "publications": {}}
    if not isinstance(value, dict):
        return {"version": _SERVING_CACHE_VERSION, "publications": {}}
    value.setdefault("version", _SERVING_CACHE_VERSION)
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
        # The public API must remain available even if the optional serving
        # cache cannot be persisted. The in-memory mobile cache still works.
        return


def _publication_snapshot(publication: Publication, *, verified: bool) -> dict:
    payload = publication.model_dump(exclude={"text", "page_texts"})
    payload["structured_offers"] = [offer.model_dump() for offer in publication.structured_offers]
    return {
        "fingerprint": publication_fingerprint(publication),
        "verified": verified,
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
    """Keep the last usable flyer visible while a replacement is processed.

    The readiness gate is version-based. A provider can therefore change a
    publication fingerprint before the detector/Luna worker has finished the
    new version. Previously that made the retailer disappear from Aviser and
    Tilbud and could leave an iPhone showing cached page images with zero
    hotspots. The serving cache decouples those states: a ready publication is
    snapshotted with all structured offers/hotspots and remains public until its
    successor is ready.

    On the first deployment of this cache there is no historical snapshot yet.
    Availability wins for that one bootstrap generation: the currently fetched
    publication is served and stored as provisional instead of blanking a
    retailer. Once the exact fingerprint becomes ready, the row is upgraded to
    verified. Later changes then correctly keep the verified predecessor.
    """
    readiness = load_readiness_store()
    if not readiness.get("initialized"):
        return publications

    store = _load_serving_cache()
    rows = store.setdefault("publications", {})
    result: list[Publication] = []
    current_ids: set[str] = set()
    changed = False

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
            ):
                rows[publication.id] = _publication_snapshot(publication, verified=True)
                changed = True
            continue

        if cached is not None and _publication_not_expired(cached):
            # Do not swap a working public flyer for an unverified replacement.
            result.append(cached)
            continue

        # Migration/bootstrap fallback: there is no predecessor to preserve.
        # Serving the deterministic provider result is safer than making the
        # whole retailer and every hotspot disappear while Luna catches up.
        result.append(publication)
        rows[publication.id] = _publication_snapshot(publication, verified=False)
        changed = True

    # A short provider outage must not remove a previously verified flyer. Keep
    # snapshots that are still valid even when the upstream source is absent in
    # this particular fetch.
    for publication_id, row in rows.items():
        if publication_id in current_ids or not isinstance(row, dict) or not row.get("verified"):
            continue
        cached = _restore_publication(row)
        if cached is not None and _publication_not_expired(cached):
            result.append(cached)

    if changed:
        _save_serving_cache(store)
    return result


def _safe_luna_variant_name(value: object) -> str | None:
    """Return only concrete named product variants from Luna.

    Weight, volume, pack count and generic campaign wording are offer metadata,
    never product identity. This mirrors Kurv's deterministic Variant Extractor
    rule and keeps the AI overlay strictly additive.
    """
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


def apply_cached_enrichment(publications: list[Publication]) -> list[Publication]:
    """Serve a stable flyer generation, then apply persisted Luna facts.

    Readiness remains independent of the Luna master switch, but processing a
    new version can no longer remove the last usable version from Kurv. The
    serving snapshot includes the structured offers and hotspot geometry used
    by the native iOS reader.
    """
    publications = _serve_stable_publications(publications)

    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return publications

    # One cached store snapshot per flyer fetch. Never deep-copy/re-read the
    # growing Luna store once per offer; page audits can carry thousands of facts.
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
                    # The multi-product fact is useful even while a crop is
                    # pending: it can only make the UI safer by blocking
                    # direct-add, never invent a specific variant.
                    signals.append("luna-multiple-products")
                if facts.get("package_size"):
                    # Keep package/weight as metadata only. Product Identity and
                    # Price Guard never consume this signal as identity evidence.
                    signals.append("luna-package-size-known")

            if (
                not semantic_needs_crop
                and not offer.brand
                and facts.get("brand")
                and identity_confidence >= threshold
            ):
                updates["brand"] = str(facts["brand"]).strip()

            # A visually verified ordinary price can repair a provider value for
            # non-member campaigns. Member campaigns remain handled by the
            # separate member-pricing presentation layer so ordinary/member
            # roles can never collapse into one headline price.
            if (
                semantic is not None
                and not semantic_needs_crop
                and facts.get("member_price") is None
                and isinstance(facts.get("ordinary_price"), (int, float))
                and pricing_confidence >= 0.99
            ):
                updates["price"] = round(float(facts["ordinary_price"]), 2)

            # Strong deterministic variants remain protected. Luna can replace
            # a weak campaign heading or empty provider set only at high visual
            # confidence. Size/weight/generic phrases are filtered.
            if (
                not semantic_needs_crop
                and variant_confidence >= 0.99
                and offer.variant_confidence < 0.90
            ):
                names = [
                    name
                    for value in facts.get("variants", [])
                    if (name := _safe_luna_variant_name(value)) is not None
                ]
                names = list(dict.fromkeys(names))[:12]
                if names:
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

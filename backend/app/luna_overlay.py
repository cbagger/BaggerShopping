from __future__ import annotations

import hashlib
import re

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
    """Apply only persisted, high-confidence Luna facts.

    Build 58 adds a general semantic page audit. Provider facts remain the base
    truth and are never mutated on disk. The overlay is an in-memory copy used
    only while Luna is enabled. Turning Luna OFF therefore immediately restores
    the deterministic provider/Variant Extractor path, including all original
    prices, brands and variants.
    """
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return publications

    # One cached store snapshot per flyer fetch. Never deep-copy/re-read the
    # growing Luna store once per offer; Build 58 can carry thousands of facts.
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
                    # The multi-product fact is intentionally useful even while
                    # a crop is pending: it can only make the UI safer by
                    # blocking direct-add, never invent a specific variant.
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
            # separate member-pricing presentation layer so ordinary/member roles
            # can never collapse into one headline price.
            if (
                semantic is not None
                and not semantic_needs_crop
                and facts.get("member_price") is None
                and isinstance(facts.get("ordinary_price"), (int, float))
                and pricing_confidence >= 0.99
            ):
                updates["price"] = round(float(facts["ordinary_price"]), 2)

            # Strong deterministic variants remain protected. Luna can replace a
            # weak campaign heading or empty provider variant set when the visual
            # audit has high confidence. Size/weight/generic phrases are filtered.
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

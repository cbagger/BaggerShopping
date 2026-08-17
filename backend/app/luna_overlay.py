from __future__ import annotations

import hashlib
import re

from .luna_enrichment import load_config, load_store, offer_fingerprint
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
    r"^\s*(?:flere\s+varianter|frit\s+valg|assorterede?|diverse)\s*$",
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


def apply_cached_enrichment(publications: list[Publication]) -> list[Publication]:
    """Overlay only facts that are safe for existing deterministic engines.

    Pricing remains handled by member_pricing's synchronous overlay so source
    price fields and Price Guard identity rules are never silently rewritten.
    Product names are likewise retained. High-confidence brand/variant facts may
    enrich weak provider records because these improve search/variant selection
    without changing hotspot geometry or the chosen product identity root.
    """
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return publications

    records = load_store().get("records", {})
    threshold = float(config.get("min_apply_confidence", 0.96))
    result: list[Publication] = []

    for publication in publications:
        changed = False
        offers: list[Offer] = []
        for offer in publication.structured_offers:
            row = records.get(offer_fingerprint(offer))
            facts = row.get("facts") if isinstance(row, dict) and row.get("status") == "completed" else None
            if not isinstance(facts, dict) or not facts.get("same_offer"):
                offers.append(offer)
                continue

            updates: dict = {}
            identity_confidence = float(facts.get("identity_confidence") or 0)
            variant_confidence = float(facts.get("variant_confidence") or 0)

            if not offer.brand and facts.get("brand") and identity_confidence >= threshold:
                updates["brand"] = str(facts["brand"]).strip()

            # Protect the strong text-only Variant Extractor v2. Luna can fill a
            # genuinely weak/empty provider variant set, but cannot replace a
            # variant result that Kurv already considers reliable. Weight,
            # volume, pack count and generic campaign wording are never variants.
            if variant_confidence >= 0.99 and offer.variant_confidence < 0.65:
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
                    updates["quality_signals"] = list(dict.fromkeys([
                        *offer.quality_signals,
                        "luna-verified-variants",
                    ]))

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

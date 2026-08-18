from __future__ import annotations

"""Backward-compatible import surface for Variant Intelligence v3."""

from .flyer_intelligence import VariantCandidate
from .variant_extractor_v3 import extract_variants_v3


_SOURCE_COMPATIBILITY = {
    "structured-products-v3": "structured-products",
    "heading-v3": "heading",
    "description-text-v3": "description-text",
    "campaign-heading-v3": "campaign-heading",
}


def extract_variants_v2(
    identity: str,
    heading: str,
    description: str | None = None,
    *,
    payload: object = None,
):
    values = extract_variants_v3(
        identity,
        heading,
        description,
        payload=payload,
    )
    return [
        VariantCandidate(
            id=value.id,
            name=value.name,
            confidence=value.confidence,
            source=_SOURCE_COMPATIBILITY.get(value.source, value.source),
        )
        for value in values
    ]


__all__ = ["extract_variants_v2"]

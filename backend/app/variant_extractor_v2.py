from __future__ import annotations

"""Backward-compatible import surface for Variant Intelligence v3.

The production call site historically imported ``extract_variants_v2`` from
this module. Keep that symbol while routing all callers through the first-class
v3 implementation so old imports/tests do not create a second behaviour path.
"""

from .variant_extractor_v3 import extract_variants_v3


def extract_variants_v2(
    identity: str,
    heading: str,
    description: str | None = None,
    *,
    payload: object = None,
):
    return extract_variants_v3(
        identity,
        heading,
        description,
        payload=payload,
    )


__all__ = ["extract_variants_v2"]

from __future__ import annotations

from . import luna_enrichment
from .luna_pricing_reader import member_pricing_override as member_pricing_override_fast


def install() -> None:
    """Temporary compatibility bridge for the existing mobile runtime.

    The read implementation now lives behind the stable ``luna_pricing_reader``
    API and no longer reaches into private Luna worker cache internals. A later
    architecture-hardening step removes this assignment entirely by wiring the
    reader directly into the pricing service.
    """

    luna_enrichment.member_pricing_override = member_pricing_override_fast


__all__ = ["install", "member_pricing_override_fast"]

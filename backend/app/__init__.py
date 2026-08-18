"""Kurv backend compatibility hooks still awaiting first-class provider wiring.

Customer offer serialization, Luna pricing, Samsung request policy, product
identity caching and core flyer intelligence now use explicit normal code paths.
The remaining hooks are limited to provider membership-context enrichment and
the cached Luna flyer overlay, and are removed in the following hardening
tranches.
"""

from __future__ import annotations

from . import meny_flyer as _mf
from .member_pricing_sources import (
    enrich_ipaper_offers,
    enrich_schwarz_publication,
    enrich_tjek_offers,
)


# Membership price recognition needs the text surrounding the actual advert,
# not a whole-page image recognizer. The provider adapters already carry page
# text/OCR/structured metadata but historically discarded some of it before an
# Offer reached the public API. Enrich only raw_text; hotspot geometry, variant
# extraction and source prices remain untouched.
_original_parse_enrichment_chunks = _mf.parse_enrichment_chunks


def _member_context_parse_enrichment_chunks(publication, chunks):
    offers = _original_parse_enrichment_chunks(publication, chunks)
    return enrich_ipaper_offers(publication, offers)


_mf.parse_enrichment_chunks = _member_context_parse_enrichment_chunks

from . import flyer_adapters as _fa  # noqa: E402

_original_parse_tjek_hotspots = _fa.parse_tjek_hotspots
_original_publication_from_schwarz = _fa._publication_from_schwarz


def _member_context_parse_tjek_hotspots(publication, rows, offer_rows=None):
    offers = _original_parse_tjek_hotspots(publication, rows, offer_rows)
    return enrich_tjek_offers(offers, rows, offer_rows)


def _member_context_publication_from_schwarz(payload, source, reader_url):
    publication = _original_publication_from_schwarz(payload, source, reader_url)
    return enrich_schwarz_publication(publication, payload)


_fa.parse_tjek_hotspots = _member_context_parse_tjek_hotspots
_fa._publication_from_schwarz = _member_context_publication_from_schwarz


# Luna is an optional cached overlay, never a flyer-discovery dependency. Keep
# the raw deterministic fetcher exported for the Luna worker, then let ordinary
# Kurv consumers see only already-verified cached brand/variant enrichment. The
# overlay itself makes no network/OpenAI calls, and returns the original objects
# unchanged while Luna is OFF.
from .luna_overlay import apply_cached_enrichment as _apply_cached_luna_enrichment  # noqa: E402

_original_fetch_all_publications = _fa.fetch_all_publications


async def _luna_aware_fetch_all_publications(*args, **kwargs):
    publications = await _original_fetch_all_publications(*args, **kwargs)
    try:
        return _apply_cached_luna_enrichment(publications)
    except Exception:
        # AI telemetry/cache corruption must never reduce normal Kurv function.
        return publications


_fa.fetch_all_publications = _luna_aware_fetch_all_publications

from __future__ import annotations

from . import mobile_offers
from .meny_flyer import Offer
from .product_identity import MatchResult


_ORIGINAL_OFFER_PAYLOAD = mobile_offers._offer_payload


def reader_offer_payload(
    offer: Offer,
    identity: MatchResult | None = None,
    publication_status: str | None = None,
) -> dict:
    """Build the flyer-reader payload without recomputing product identity.

    The native flyer reader only needs the source offer, variants, member-price
    metadata, quality data and hotspot geometry in order to render and add an
    offer. Product-identity analysis is useful for search/matching, but doing it
    again for every offer while opening a flyer makes the endpoint slower than
    the iPhone's request timeout on the QNAP.

    Search/matching calls pass identity/publication_status and therefore keep
    the complete historic payload unchanged.
    """
    if identity is not None or publication_status is not None:
        return _ORIGINAL_OFFER_PAYLOAD(offer, identity, publication_status)

    payload = offer.model_dump()
    learning = mobile_offers.learned_adjustment(
        mobile_offers.load_feedback_store(mobile_offers._QUALITY_STORE_PATH),
        offer.retailer,
        offer.quality_source,
        offer.publication_id,
    )
    payload["quality_score"] = round(
        max(0.0, min(1.0, offer.quality_score + learning.score)),
        3,
    )
    payload["hotspot_confidence"] = round(
        max(0.0, offer.hotspot_confidence - min(0.18, learning.position_reports * 0.02)),
        3,
    )
    payload["variant_confidence"] = round(
        max(0.0, offer.variant_confidence - min(0.18, learning.variant_reports * 0.02)),
        3,
    )
    payload["learning_reports"] = {
        "wrong_position": learning.position_reports,
        "wrong_variants": learning.variant_reports,
    }
    return payload


def install() -> None:
    """Install the reader fast path after mobile_offers has registered routes."""
    mobile_offers._offer_payload = reader_offer_payload

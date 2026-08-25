from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from .member_pricing import detect_member_pricing
from .meny_flyer import Offer


MEMBER_PRESENTATION_FIELDS = {
    "member_price",
    "member_price_label",
    "member_price_app",
    "member_price_requires_activation",
    "member_price_source",
}


def offer_is_upcoming(offer: Offer, *, today: date | None = None) -> bool:
    """Return whether an offer-level start date is still in the future.

    Publication validity is intentionally not used here. A flyer may already be
    current while one campaign on a later page starts several days later.
    """

    value = (offer.valid_from or "").strip()
    if not value:
        return False
    try:
        start = datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return False
    return start > (today or date.today())


def raw_offer_payload(offer: Offer, **model_dump_kwargs: Any) -> dict[str, Any]:
    """Serialize only provider/source fields from an Offer.

    Raw serialization is deliberately independent of customer presentation.
    Serving caches, worker snapshots and diagnostics can therefore persist an
    Offer without accidentally freezing derived Luna/member-pricing metadata.
    """

    return BaseModel.model_dump(offer, **model_dump_kwargs)


def _apply_offer_validity_guard(payload: dict[str, Any], offer: Offer) -> None:
    """Fail closed for a campaign that has not started yet.

    Legacy iPhone builds must keep seeing the normal hotspot fields as ``null``
    so they cannot expose an add button for a future offer. Newer builds can use
    the separate display-only hotspot fields to draw the marker while still
    respecting ``safe_to_add = false``.
    """

    if not offer_is_upcoming(offer):
        return
    payload["safe_to_add"] = False
    payload["publication_status"] = "upcoming"
    for key in ("hotspot_x", "hotspot_y", "hotspot_width", "hotspot_height"):
        value = payload.get(key)
        if value is not None:
            payload[f"display_{key}"] = value
        payload[key] = None


def customer_offer_payload(offer: Offer, **model_dump_kwargs: Any) -> dict[str, Any]:
    """Serialize one Offer for the iPhone/customer API.

    Membership pricing is presentation metadata. It is derived at the explicit
    API boundary instead of changing ``Offer.model_dump`` globally at import
    time. The original provider price remains untouched on the model itself.
    """

    payload = raw_offer_payload(offer, **model_dump_kwargs)
    _apply_offer_validity_guard(payload, offer)

    text = " ".join(filter(None, (offer.product_name, offer.raw_text)))
    pricing = detect_member_pricing(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=text,
        unit_price=offer.unit_price,
    )
    if pricing is None:
        return payload

    payload["price"] = pricing.ordinary_price
    payload["member_price"] = pricing.member_price
    payload["member_price_label"] = pricing.label
    payload["member_price_app"] = pricing.app_name
    payload["member_price_requires_activation"] = pricing.requires_activation
    payload["member_price_source"] = pricing.source
    return payload


__all__ = [
    "MEMBER_PRESENTATION_FIELDS",
    "customer_offer_payload",
    "offer_is_upcoming",
    "raw_offer_payload",
]

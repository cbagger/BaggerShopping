from __future__ import annotations

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


def raw_offer_payload(offer: Offer, **model_dump_kwargs: Any) -> dict[str, Any]:
    """Serialize only provider/source fields from an Offer.

    Raw serialization is deliberately independent of customer presentation.
    Serving caches, worker snapshots and diagnostics can therefore persist an
    Offer without accidentally freezing derived Luna/member-pricing metadata.
    """

    return BaseModel.model_dump(offer, **model_dump_kwargs)


def customer_offer_payload(offer: Offer, **model_dump_kwargs: Any) -> dict[str, Any]:
    """Serialize one Offer for the iPhone/customer API.

    Membership pricing is presentation metadata. It is derived at the explicit
    API boundary instead of changing ``Offer.model_dump`` globally at import
    time. The original provider price remains untouched on the model itself.
    """

    payload = raw_offer_payload(offer, **model_dump_kwargs)
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
    "raw_offer_payload",
]

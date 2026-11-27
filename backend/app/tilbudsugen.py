from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from .meny_flyer import Offer, OfferVariant


SOURCE = "tilbudsugen"
_SEARCH_URL = "https://www.tilbudsugen.dk/api/ai/v1/search"
_TIMEZONE = ZoneInfo("Europe/Copenhagen")
RETAILERS = {
    "365discount": ("365discount", "https://365discount.coop.dk/365avis/"),
    "REMA 1000": ("rema_1000", "https://shop.rema1000.dk/avisvarer"),
    "Netto": ("netto", "https://netto.dk/netto-avisen/"),
}
_UNITS = {"kg": "kg", "g": "g", "ltr": "l", "l": "l", "ml": "ml", "cl": "cl", "stk": "stk"}


def _local_date(value: object) -> date | None:
    # Tilbudsugen encodes a Danish calendar day as a UTC date boundary.
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def _money(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return None


def _parse_offer(row: dict, retailers: set[str], today: date) -> Offer | None:
    availability = row.get("availability") or {}
    retailer = availability.get("retail_chain")
    if retailer not in retailers or availability.get("is_border_shop") or row.get("currency") != "DKK":
        return None

    name = " ".join(str(row.get("product_name") or "").split())
    price = _money(row.get("price"))
    start = _local_date(row.get("start_date"))
    end = _local_date(row.get("end_date"))
    if not name or price is None or not start or not end or end < max(start, today):
        return None

    promo = row.get("promotion") or {}
    minimum = promo.get("minQuantity")
    minimum = minimum if type(minimum) is int and minimum > 1 else None
    if minimum is not None:
        price = _money(promo.get("totalPrice"))
        if price is None:
            return None

    member = promo.get("requiresMembership") is True
    note = " ".join(str(promo.get("rawText") or "").split())
    if member:
        note = f"Medlemspris {price:.2f} kr. {note}"
    elif re.search(r"medlemspris|medlemsrabat|kun med netto\+|app-pris", note, re.IGNORECASE):
        # An ambiguous provider price must not be displayed as ordinary.
        return None

    metrics = row.get("unit_metrics") or {}
    unit = _UNITS.get(str(metrics.get("unit_type") or "").rstrip(".").casefold())
    quantity = _money(metrics.get("quantity")) if unit and minimum is None else None
    brand = row.get("brand") or None
    slug, source_url = RETAILERS[retailer]
    publication_id = f"tilbudsugen-{slug}-{start.isoformat()}-{end.isoformat()}"
    identity = hashlib.sha256(
        f"{publication_id}|{name}|{brand}|{price}|{quantity}|{unit}|{minimum}|{member}".encode()
    ).hexdigest()[:20]
    safe_to_add = not member and minimum is None
    return Offer(
        id=identity,
        retailer=retailer,
        publication_id=publication_id,
        publication_title=f"{retailer} tilbud",
        valid_from=start.strftime("%d.%m.%Y"),
        valid_until=end.strftime("%d.%m.%Y"),
        product_name=name,
        brand=brand,
        price=price,
        quantity=quantity,
        unit=unit if quantity is not None else None,
        source_url=source_url,
        raw_text=note or name,
        safe_to_add=safe_to_add,
        minimum_purchase_quantity=minimum,
        variants=[OfferVariant(id=identity, name=f"{brand} {name}" if brand and brand.casefold() not in name.casefold() else name)]
        if safe_to_add else [],
        quality_source=SOURCE,
    )


async def fetch_offers(term: str, retailers: list[str]) -> list[tuple[Offer, str]]:
    # One lookup for all selected chains. Forward only the product query and filters.
    filters = ",".join(RETAILERS[retailer][0] for retailer in retailers)
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(_SEARCH_URL, params={"q": term, "chains": filters})
        response.raise_for_status()
        items = response.json()["items"]
    if not isinstance(items, list):
        raise ValueError("Tilbudsugen returned an invalid offer list")
    today = datetime.now(_TIMEZONE).date()
    selected = set(retailers)
    result = []
    for group in items:
        for row in group["offers"]:
            if offer := _parse_offer(row, selected, today):
                start = _local_date(row.get("start_date"))
                result.append((offer, "upcoming" if start and start > today else "current"))
    return result

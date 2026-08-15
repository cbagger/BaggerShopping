"""Kurv backend package compatibility hooks.

Hotspot recall is intentionally biased toward showing an extra '+' rather than
silently dropping a valid offer.  The stricter flyer-intelligence v2 geometry
and duplicate coupling introduced regressions for Tjek/Coop catalogues, so this
small compatibility layer restores the permissive pre-v2 behaviour while the
rest of flyer intelligence remains intact.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import flyer_intelligence as _fi


def _recall_first_box_from_polygon(
    points: Iterable[Sequence[object]],
    *,
    vertical_scale: float = 1.0,
    source: str = "native-polygon",
):
    """Build a hotspot box using the permissive pre-v2 clamping semantics.

    Provider polygons occasionally overshoot page edges by a little or are very
    small.  Those are still useful for placing a '+' and must not be discarded.
    """
    if vertical_scale <= 0 or vertical_scale != vertical_scale:
        return None

    parsed: list[tuple[float, float]] = []
    for point in points:
        if len(point) < 2:
            continue
        x = _fi._number(point[0])
        y = _fi._number(point[1])
        if x is not None and y is not None:
            parsed.append((x, y / vertical_scale))

    if len(parsed) < 2:
        return None

    xs, ys = zip(*parsed)
    x = max(0.0, min(1.0, min(xs)))
    y = max(0.0, min(1.0, min(ys)))
    width = max(0.0001, min(1.0 - x, max(xs) - min(xs)))
    height = max(0.0001, min(1.0 - y, max(ys) - min(ys)))

    if width <= 0 or height <= 0:
        return None

    area = width * height
    confidence = 0.97 if source in {"native", "tjek-polygon", "ipaper-marker"} else 0.82
    if area > 0.65:
        confidence -= 0.25
    elif area < 0.0015:
        confidence -= 0.12

    return _fi.HotspotBox(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=max(0.35, confidence),
        source=source,
    )


def _recall_first_couple_offers(offers):
    """Keep every provider hotspot instead of collapsing possible valid rows."""
    return sorted(
        list(offers),
        key=lambda value: (
            value.page_number or 0,
            value.hotspot_y or 0,
            value.hotspot_x or 0,
        ),
    )


# flyer_adapters imports these symbols after package initialisation, therefore
# replacing them here changes only runtime parsing behaviour without reverting
# the newer variant, OCR, quality and feedback machinery.
_fi.box_from_polygon = _recall_first_box_from_polygon
_fi.couple_offers = _recall_first_couple_offers

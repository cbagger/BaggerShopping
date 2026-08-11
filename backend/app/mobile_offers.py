from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .meny_flyer import fetch_meny_flyer, search_publication


router = APIRouter(prefix="/api/mobile/v1/offers", tags=["offers"])


def _publication_payload(publication):
    return {
        "title": publication.title,
        "valid_from": publication.valid_from,
        "valid_until": publication.valid_until,
        "content_source": getattr(publication, "content_source", None),
        "page_count": getattr(publication, "page_count", None),
    }


@router.get("/meny")
async def meny_offer_status():
    try:
        publication = await fetch_meny_flyer()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MENY flyer unavailable: {exc}") from exc
    return {
        "ok": True,
        "retailer": "MENY",
        "publication": _publication_payload(publication),
    }


@router.get("/meny/search")
async def meny_offer_search(q: str = Query(min_length=1, max_length=100)):
    try:
        publication = await fetch_meny_flyer()
        result = search_publication(publication, q)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MENY flyer unavailable: {exc}") from exc
    return {
        "ok": True,
        "retailer": "MENY",
        "query": q,
        "publication": _publication_payload(publication),
        "match_count": len(result.matches),
        "matches": result.matches,
    }

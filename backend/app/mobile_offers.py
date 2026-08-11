from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .meny_flyer import Publication, fetch_meny_flyer, search_publication


router = APIRouter(prefix="/api/mobile/v1/offers", tags=["offers"])


def _publication_payload(publication: Publication) -> dict:
    return publication.model_dump(exclude={"text", "page_texts"})


async def _publication() -> Publication:
    try:
        return await fetch_meny_flyer()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MENY flyer unavailable: {exc}") from exc


@router.get("/publications")
async def publications():
    publication = await _publication()
    return {"ok": True, "publications": [_publication_payload(publication)]}


@router.get("/search")
async def search_offers(
    q: str = Query(min_length=1, max_length=100),
    retailer: str = Query(default="MENY", max_length=40),
):
    if retailer.casefold() != "meny":
        raise HTTPException(status_code=400, detail="Retailer is not supported yet")
    publication = await _publication()
    result = search_publication(publication, q)
    return {
        "ok": True,
        "query": q,
        "retailer": "MENY",
        "publication": _publication_payload(publication),
        "offer_count": len(result.offers),
        "offers": [offer.model_dump() for offer in result.offers],
    }


@router.get("/publications/{publication_id}/offers")
async def publication_offers(publication_id: str):
    publication = await _publication()
    if publication.id != publication_id:
        raise HTTPException(status_code=404, detail="Publication is no longer current")
    return {
        "ok": True,
        "publication": _publication_payload(publication),
        "offers": [offer.model_dump() for offer in publication.structured_offers],
    }


# Compatibility for the already deployed proof-of-concept iOS build.
@router.get("/meny")
async def meny_offer_status():
    publication = await _publication()
    return {"ok": True, "retailer": "MENY", "publication": _publication_payload(publication)}


@router.get("/meny/search")
async def meny_offer_search(q: str = Query(min_length=1, max_length=100)):
    publication = await _publication()
    result = search_publication(publication, q)
    return {
        "ok": True,
        "retailer": "MENY",
        "query": q,
        "publication": _publication_payload(publication),
        "match_count": len(result.offers),
        "matches": [offer.raw_text for offer in result.offers],
    }

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .households import LEGACY_HOUSEHOLD_ID, current_household


router = APIRouter(prefix="/api/mobile/v1", tags=["mobile-offer-metadata"])
offer_metadata_store_lock = asyncio.Lock()


class OfferMetadataRecord(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    retailer: str = Field(min_length=1, max_length=100)
    price: float | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    offer_id: str | None = None
    publication_id: str | None = None
    matched_item_name: str | None = None
    offer_snapshot: dict[str, object] | None = None


class OfferMetadataResponse(BaseModel):
    ok: bool = True
    metadata: list[OfferMetadataRecord]


class OfferMetadataRemoveRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)


class OfferMetadataSyncRequest(BaseModel):
    metadata: list[OfferMetadataRecord]


class RenameShoppingItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def offer_metadata_key(item_name: str) -> str:
    return " ".join(item_name.casefold().strip().split())


def offer_metadata_store_path() -> Path:
    base = Path(os.environ.get("OFFER_METADATA_STORE_PATH", "/data/offer-metadata.json"))
    household_id = current_household().household_id
    if household_id == LEGACY_HOUSEHOLD_ID:
        return base
    return base.with_name(f"{base.stem}-{household_id}{base.suffix}")


def load_offer_metadata_store() -> dict[str, dict[str, object]]:
    path = offer_metadata_store_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    result: dict[str, dict[str, object]] = {}
    for raw_key, raw_value in raw.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, dict):
            continue
        try:
            record = OfferMetadataRecord.model_validate(raw_value)
        except Exception:
            continue
        key = offer_metadata_key(record.item_name)
        if key:
            result[key] = record.model_dump()
    return result


def save_offer_metadata_store(store: dict[str, dict[str, object]]) -> None:
    path = offer_metadata_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def normalized_record(record: OfferMetadataRecord) -> OfferMetadataRecord:
    item_name = record.item_name.strip()
    retailer = record.retailer.strip()
    if not item_name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")
    if not retailer:
        raise HTTPException(status_code=422, detail="Retailer cannot be empty")
    return record.model_copy(
        update={
            "item_name": item_name,
            "retailer": retailer,
            "matched_item_name": record.matched_item_name.strip() if record.matched_item_name else None,
        }
    )


@router.get("/offer-metadata", response_model=OfferMetadataResponse)
async def get_offer_metadata() -> OfferMetadataResponse:
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
    records = [OfferMetadataRecord.model_validate(value) for value in store.values()]
    records.sort(key=lambda value: offer_metadata_key(value.item_name))
    return OfferMetadataResponse(metadata=records)


@router.put("/offer-metadata")
async def put_offer_metadata(record: OfferMetadataRecord) -> dict[str, object]:
    clean = normalized_record(record)
    key = offer_metadata_key(clean.item_name)
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        store[key] = clean.model_dump()
        save_offer_metadata_store(store)
    return {"ok": True, "item_name": clean.item_name}


@router.put("/offer-metadata/sync", response_model=OfferMetadataResponse)
async def sync_offer_metadata(request: OfferMetadataSyncRequest) -> OfferMetadataResponse:
    """Merge missing device metadata into the shared store without overwriting server values."""
    clean_records = [normalized_record(record) for record in request.metadata]
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        changed = False
        for record in clean_records:
            key = offer_metadata_key(record.item_name)
            if key not in store:
                store[key] = record.model_dump()
                changed = True
        if changed:
            save_offer_metadata_store(store)
        records = [OfferMetadataRecord.model_validate(value) for value in store.values()]
    records.sort(key=lambda value: offer_metadata_key(value.item_name))
    return OfferMetadataResponse(metadata=records)


@router.post("/offer-metadata/remove")
async def remove_offer_metadata(request: OfferMetadataRemoveRequest) -> dict[str, object]:
    key = offer_metadata_key(request.item_name)
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        removed = store.pop(key, None) is not None
        if removed:
            save_offer_metadata_store(store)
    return {"ok": True, "removed": removed}


@router.patch("/items/{item_id}/name")
async def rename_shopping_item(
    item_id: str,
    request: RenameShoppingItemRequest,
) -> dict[str, object]:
    """Rename one Samsung item in place and move its shared offer metadata."""
    context = current_household()
    if context.list_backend == "local":
        from .households import update_household

        new_name = " ".join(request.name.strip().split())
        if not new_name:
            raise HTTPException(status_code=422, detail="Item name cannot be empty")

        old_name: str | None = None
        def mutate(household):
            nonlocal old_name
            for item in household.setdefault("items", []):
                if item.get("id") == item_id:
                    old_name = item.get("name")
                    item["name"] = new_name
                    return
            raise HTTPException(status_code=404, detail="Varen findes ikke i familien")
        await update_household(context, mutate)
        metadata_migrated = False
        async with offer_metadata_store_lock:
            store = load_offer_metadata_store()
            raw_record = store.pop(offer_metadata_key(old_name or ""), None)
            if raw_record is not None:
                moved = OfferMetadataRecord.model_validate(raw_record).model_copy(update={"item_name": new_name})
                store[offer_metadata_key(new_name)] = moved.model_dump()
                save_offer_metadata_store(store)
                metadata_migrated = True
        return {"ok": True, "item_id": item_id, "old_name": old_name, "name": new_name, "offer_metadata_migrated": metadata_migrated}

    # Keep Samsung/config imports lazy: metadata-only mobile API routes and their
    # unit tests must not require SAMSUNG_LIST_ID just to import this router.
    from .grpc_web import _build_sync_items_update_request
    from .samsung import SamsungFoodClient, SamsungFoodError

    new_name = " ".join(request.name.strip().split())
    if not new_name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")

    client = SamsungFoodClient()
    try:
        item = await client._find_item(item_id)
        old_name = item.name
        body = _build_sync_items_update_request(
            client.list_id,
            item_id,
            new_name,
            bool(item.checked),
            int(time.time() * 1000),
            quantity=item.quantity,
            unit=item.unit,
        )
        result = await client._post_sync_items(body)
    except SamsungFoodError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    metadata_migrated = False
    old_key = offer_metadata_key(old_name)
    new_key = offer_metadata_key(new_name)
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        raw_record = store.pop(old_key, None)
        if raw_record is not None:
            record = OfferMetadataRecord.model_validate(raw_record)
            moved = record.model_copy(
                update={
                    "item_name": new_name,
                    "matched_item_name": new_name if record.matched_item_name else None,
                }
            )
            store[new_key] = moved.model_dump()
            save_offer_metadata_store(store)
            metadata_migrated = True

    return {
        "ok": True,
        "item_id": item_id,
        "old_name": old_name,
        "name": new_name,
        "offer_metadata_migrated": metadata_migrated,
        "grpc_status": result.get("grpc_status"),
    }

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .households import LEGACY_HOUSEHOLD_ID, current_household


router = APIRouter(prefix="/api/mobile/v1", tags=["mobile-offer-metadata"])
offer_metadata_store_lock = asyncio.Lock()


class OfferMetadataRecord(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    item_id: str | None = Field(default=None, max_length=300)
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
    item_id: str | None = Field(default=None, max_length=300)


class OfferMetadataSyncRequest(BaseModel):
    metadata: list[OfferMetadataRecord]


class RenameShoppingItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _normalized_item_id(item_id: str | None) -> str | None:
    value = "".join((item_id or "").strip().split())
    return value.casefold() or None


def offer_metadata_key(item_name: str, item_id: str | None = None) -> str:
    """Return a stable server key, preferring the immutable list-item ID.

    Historic stores remain valid because records without ``item_id`` keep the
    exact old normalized-name key. New/updated records are promoted to an ID
    key as soon as Samsung exposes one.
    """
    normalized_id = _normalized_item_id(item_id)
    if normalized_id:
        return f"item:{normalized_id}"
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
        key = offer_metadata_key(record.item_name, record.item_id)
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
    item_name = " ".join(record.item_name.strip().split())
    retailer = record.retailer.strip()
    item_id = "".join(record.item_id.strip().split()) if record.item_id else None
    if not item_name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")
    if not retailer:
        raise HTTPException(status_code=422, detail="Retailer cannot be empty")
    return record.model_copy(
        update={
            "item_name": item_name,
            "item_id": item_id or None,
            "retailer": retailer,
            "matched_item_name": record.matched_item_name.strip() if record.matched_item_name else None,
        }
    )


def _remove_aliases(
    store: dict[str, dict[str, object]],
    *,
    item_name: str,
    item_id: str | None,
    keep_key: str | None = None,
) -> bool:
    changed = False
    for key in {
        offer_metadata_key(item_name),
        offer_metadata_key(item_name, item_id) if item_id else "",
    }:
        if key and key != keep_key and store.pop(key, None) is not None:
            changed = True
    return changed


def reconcile_offer_metadata_items(items: Iterable[object]) -> bool:
    """Promote name-bound records to IDs and follow Samsung name normalization.

    Called after a successful list read. No fuzzy matching is used: a historic
    name-only record is promoted only on one exact normalized active name. Once
    an ID is present, that immutable ID is authoritative and the display name is
    updated to Samsung's current normalized name.
    """
    normalized_items: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            item_id = item.get("id")
            name = item.get("name")
        else:
            item_id = getattr(item, "id", None)
            name = getattr(item, "name", None)
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(name, str) or not name.strip():
            continue
        normalized_items.append((item_id.strip(), " ".join(name.strip().split())))

    if not normalized_items:
        return False

    by_id = {_normalized_item_id(item_id): (item_id, name) for item_id, name in normalized_items}
    by_name: dict[str, list[tuple[str, str]]] = {}
    for item_id, name in normalized_items:
        by_name.setdefault(offer_metadata_key(name), []).append((item_id, name))

    store = load_offer_metadata_store()
    changed = False
    next_store: dict[str, dict[str, object]] = {}

    for raw_value in store.values():
        record = OfferMetadataRecord.model_validate(raw_value)
        current = record
        normalized_id = _normalized_item_id(record.item_id)

        if normalized_id and normalized_id in by_id:
            actual_id, actual_name = by_id[normalized_id]
            if record.item_name != actual_name or record.item_id != actual_id:
                current = record.model_copy(update={"item_id": actual_id, "item_name": actual_name})
                changed = True
        elif not normalized_id:
            candidates = by_name.get(offer_metadata_key(record.item_name), [])
            if len(candidates) == 1:
                actual_id, actual_name = candidates[0]
                current = record.model_copy(update={"item_id": actual_id, "item_name": actual_name})
                changed = True

        key = offer_metadata_key(current.item_name, current.item_id)
        next_store[key] = current.model_dump()

    if next_store != store:
        changed = True
    if changed:
        save_offer_metadata_store(next_store)
    return changed


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
    key = offer_metadata_key(clean.item_name, clean.item_id)
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        _remove_aliases(
            store,
            item_name=clean.item_name,
            item_id=clean.item_id,
            keep_key=key,
        )
        store[key] = clean.model_dump()
        save_offer_metadata_store(store)
    return {"ok": True, "item_name": clean.item_name, "item_id": clean.item_id}


@router.put("/offer-metadata/sync", response_model=OfferMetadataResponse)
async def sync_offer_metadata(request: OfferMetadataSyncRequest) -> OfferMetadataResponse:
    """Merge missing device metadata without overwriting QNAP-owned facts."""
    clean_records = [normalized_record(record) for record in request.metadata]
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        changed = False
        for record in clean_records:
            id_key = offer_metadata_key(record.item_name, record.item_id)
            name_key = offer_metadata_key(record.item_name)
            if record.item_id and id_key not in store and name_key in store:
                existing = OfferMetadataRecord.model_validate(store.pop(name_key))
                promoted = existing.model_copy(
                    update={"item_id": record.item_id, "item_name": record.item_name}
                )
                store[id_key] = promoted.model_dump()
                changed = True
            elif id_key not in store:
                store[id_key] = record.model_dump()
                changed = True
        if changed:
            save_offer_metadata_store(store)
        records = [OfferMetadataRecord.model_validate(value) for value in store.values()]
    records.sort(key=lambda value: offer_metadata_key(value.item_name))
    return OfferMetadataResponse(metadata=records)


@router.post("/offer-metadata/remove")
async def remove_offer_metadata(request: OfferMetadataRemoveRequest) -> dict[str, object]:
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        removed = _remove_aliases(
            store,
            item_name=request.item_name,
            item_id=request.item_id,
        )
        if removed:
            save_offer_metadata_store(store)
    return {"ok": True, "removed": removed}


@router.patch("/items/{item_id}/name")
async def rename_shopping_item(
    item_id: str,
    request: RenameShoppingItemRequest,
) -> dict[str, object]:
    """Rename one shopping item in place while retaining its offer binding."""
    context = current_household()
    new_name = " ".join(request.name.strip().split())
    if not new_name:
        raise HTTPException(status_code=422, detail="Item name cannot be empty")

    if context.list_backend == "local":
        from .households import update_household

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
            id_key = offer_metadata_key(old_name or new_name, item_id)
            name_key = offer_metadata_key(old_name or "")
            source_key = id_key if id_key in store else name_key
            raw_record = store.pop(source_key, None)
            if raw_record is not None:
                moved = OfferMetadataRecord.model_validate(raw_record).model_copy(
                    update={"item_id": item_id, "item_name": new_name}
                )
                store[offer_metadata_key(new_name, item_id)] = moved.model_dump()
                save_offer_metadata_store(store)
                metadata_migrated = True
        return {
            "ok": True,
            "item_id": item_id,
            "old_name": old_name,
            "name": new_name,
            "offer_metadata_migrated": metadata_migrated,
        }

    from .grpc_web import _build_sync_items_update_request
    from .samsung import SamsungFoodClient, SamsungFoodError

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
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        id_key = offer_metadata_key(old_name, item_id)
        name_key = offer_metadata_key(old_name)
        source_key = id_key if id_key in store else name_key
        raw_record = store.pop(source_key, None)
        if raw_record is not None:
            record = OfferMetadataRecord.model_validate(raw_record)
            moved = record.model_copy(
                update={
                    "item_id": item_id,
                    "item_name": new_name,
                    "matched_item_name": new_name if record.matched_item_name else None,
                }
            )
            store[offer_metadata_key(new_name, item_id)] = moved.model_dump()
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

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .households import LEGACY_HOUSEHOLD_ID, current_household, read_household


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
    # An offer explicitly selected by a person is authoritative for that list
    # item. Old iOS builds do not send this field, so records with an offer +
    # publication reference are treated as pinned by _record_is_pinned().
    pinned: bool = False


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


_PACK_BINDING_SUFFIX_RE = re.compile(
    r"\s+\d+(?:[,.]\d+)?\s*(?:[-–]\s*)?(?:pak|pakke|pakker|pk|stk|styk|stykker)\.?\s*$",
    re.IGNORECASE,
)


def _normalized_item_id(item_id: str | None) -> str | None:
    value = "".join((item_id or "").strip().split())
    return value.casefold() or None


def offer_metadata_key(item_name: str, item_id: str | None = None) -> str:
    normalized_id = _normalized_item_id(item_id)
    if normalized_id:
        return f"item:{normalized_id}"
    return " ".join(item_name.casefold().strip().split())


def offer_binding_name_key(item_name: str) -> str:
    """Stable binding alias for Samsung-normalized terminal pack wording.

    Samsung Food may turn a deliberately selected shopping label such as
    ``Hamburger Buns 6-pak`` back into ``Hamburger Buns``. That normalization
    must not detach the offer the user selected in Kurv. Only a terminal pack /
    piece suffix is ignored here; weights, volumes, flavours and all other
    product identity remain distinct.
    """
    normalized = " ".join(item_name.casefold().strip().split())
    normalized = _PACK_BINDING_SUFFIX_RE.sub("", normalized).strip(" -–,.;:")
    return normalized


def _record_is_pinned(record: OfferMetadataRecord) -> bool:
    # Backward compatibility for Build 61 and earlier: every record written from
    # Tilbud/Aviser carried the concrete offer/publication pair even before the
    # explicit `pinned` field existed.
    return bool(record.pinned or (record.offer_id and record.publication_id))


def _pinned_binding_matches(
    store: dict[str, dict[str, object]],
    *,
    item_name: str,
    item_id: str | None = None,
) -> list[tuple[str, OfferMetadataRecord]]:
    wanted_id = _normalized_item_id(item_id)
    wanted_name = offer_metadata_key(item_name)
    wanted_binding = offer_binding_name_key(item_name)
    matches: list[tuple[str, OfferMetadataRecord]] = []
    for key, raw in store.items():
        if not isinstance(raw, dict):
            continue
        try:
            record = OfferMetadataRecord.model_validate(raw)
        except Exception:
            continue
        if not _record_is_pinned(record):
            continue
        record_id = _normalized_item_id(record.item_id)
        same_id = bool(wanted_id and record_id == wanted_id)
        same_name = offer_metadata_key(record.item_name) == wanted_name
        same_binding = bool(
            wanted_binding
            and offer_binding_name_key(record.item_name) == wanted_binding
        )
        if same_id or same_name or same_binding:
            matches.append((key, record))
    return matches


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
            "pinned": _record_is_pinned(record),
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
    candidate_keys = {
        offer_metadata_key(item_name),
        offer_metadata_key(item_name, item_id) if item_id else "",
    }
    normalized_name = offer_metadata_key(item_name)
    for key, raw in list(store.items()):
        if key in candidate_keys:
            if key != keep_key:
                store.pop(key, None)
                changed = True
            continue
        if item_id is None and isinstance(raw, dict):
            try:
                record = OfferMetadataRecord.model_validate(raw)
            except Exception:
                continue
            if offer_metadata_key(record.item_name) == normalized_name and key != keep_key:
                store.pop(key, None)
                changed = True
    return changed


def reconcile_offer_metadata_items(items: Iterable[object]) -> bool:
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
    by_binding: dict[str, list[tuple[str, str]]] = {}
    for item_id, name in normalized_items:
        by_name.setdefault(offer_metadata_key(name), []).append((item_id, name))
        binding = offer_binding_name_key(name)
        if binding:
            by_binding.setdefault(binding, []).append((item_id, name))

    store = load_offer_metadata_store()
    changed = False
    next_store: dict[str, dict[str, object]] = {}

    for raw_value in store.values():
        record = OfferMetadataRecord.model_validate(raw_value)
        current = record
        normalized_id = _normalized_item_id(record.item_id)
        pinned = _record_is_pinned(record)

        if normalized_id and normalized_id in by_id:
            actual_id, actual_name = by_id[normalized_id]
            if record.item_name != actual_name or record.item_id != actual_id or record.pinned != pinned:
                current = record.model_copy(
                    update={"item_id": actual_id, "item_name": actual_name, "pinned": pinned}
                )
                changed = True
        elif not normalized_id:
            candidates = by_name.get(offer_metadata_key(record.item_name), [])
            # Explicitly selected offers get one additional, deliberately narrow
            # reconciliation path for Samsung's terminal pack-name normalization.
            if not candidates and pinned:
                candidates = by_binding.get(offer_binding_name_key(record.item_name), [])
            if len(candidates) == 1:
                actual_id, actual_name = candidates[0]
                current = record.model_copy(
                    update={"item_id": actual_id, "item_name": actual_name, "pinned": pinned}
                )
                changed = True
            elif pinned and not record.pinned:
                current = record.model_copy(update={"pinned": True})
                changed = True

        key = offer_metadata_key(current.item_name, current.item_id)
        next_store[key] = current.model_dump()

    if next_store != store:
        changed = True
    if changed:
        save_offer_metadata_store(next_store)
    return changed


async def _active_items_for_one_time_binding() -> list[object]:
    """Read active items only while legacy/name-bound metadata still exists."""
    context = current_household()
    try:
        if context.list_backend == "local":
            household = await read_household(context)
            return list(household.get("items", []))

        from .samsung_request_policy import family_samsung_client

        client = await family_samsung_client(context)
        if client is None:
            from .samsung import SamsungFoodClient

            client = SamsungFoodClient()
        payload = await client.get_list()
        return list(payload.items)
    except Exception:
        return []


def _has_name_bound_records(store: dict[str, dict[str, object]]) -> bool:
    for raw in store.values():
        if not isinstance(raw, dict):
            continue
        try:
            if OfferMetadataRecord.model_validate(raw).item_id is None:
                return True
        except Exception:
            continue
    return False


@router.get("/offer-metadata", response_model=OfferMetadataResponse)
async def get_offer_metadata() -> OfferMetadataResponse:
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        needs_binding = _has_name_bound_records(store)

    if needs_binding:
        active_items = await _active_items_for_one_time_binding()
        if active_items:
            async with offer_metadata_store_lock:
                reconcile_offer_metadata_items(active_items)
                store = load_offer_metadata_store()
    else:
        async with offer_metadata_store_lock:
            store = load_offer_metadata_store()

    records = [OfferMetadataRecord.model_validate(value) for value in store.values()]
    records.sort(key=lambda value: offer_metadata_key(value.item_name))
    return OfferMetadataResponse(metadata=records)


@router.put("/offer-metadata")
async def put_offer_metadata(record: OfferMetadataRecord) -> dict[str, object]:
    clean = normalized_record(record)
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        pinned_matches = _pinned_binding_matches(
            store,
            item_name=clean.item_name,
            item_id=clean.item_id,
        )

        # Automatic/legacy writes must never replace an explicit user choice.
        if not clean.pinned and pinned_matches:
            _, existing = pinned_matches[0]
            return {
                "ok": True,
                "item_name": existing.item_name,
                "item_id": existing.item_id,
                "pinned_preserved": True,
            }

        # A new explicit user choice is allowed to replace the previous pin. If
        # the previous record has already been bound to Samsung's concrete item
        # ID, retain that binding even when this iPhone still knows only a name.
        if clean.pinned and len(pinned_matches) == 1:
            previous_key, previous = pinned_matches[0]
            if clean.item_id is None and previous.item_id:
                clean = clean.model_copy(
                    update={"item_id": previous.item_id, "item_name": previous.item_name}
                )
            store.pop(previous_key, None)

        key = offer_metadata_key(clean.item_name, clean.item_id)
        _remove_aliases(store, item_name=clean.item_name, item_id=clean.item_id, keep_key=key)
        store[key] = clean.model_dump()
        save_offer_metadata_store(store)
    return {
        "ok": True,
        "item_name": clean.item_name,
        "item_id": clean.item_id,
        "pinned": clean.pinned,
    }


@router.put("/offer-metadata/sync", response_model=OfferMetadataResponse)
async def sync_offer_metadata(request: OfferMetadataSyncRequest) -> OfferMetadataResponse:
    clean_records = [normalized_record(record) for record in request.metadata]
    async with offer_metadata_store_lock:
        store = load_offer_metadata_store()
        changed = False
        for record in clean_records:
            # QNAP remains authoritative during one-time/local cache migration.
            # In particular, a family-shared pinned selection must never be
            # replaced or duplicated by stale metadata from another iPhone.
            if _pinned_binding_matches(
                store,
                item_name=record.item_name,
                item_id=record.item_id,
            ):
                continue

            id_key = offer_metadata_key(record.item_name, record.item_id)
            name_key = offer_metadata_key(record.item_name)
            if record.item_id and id_key not in store and name_key in store:
                existing = OfferMetadataRecord.model_validate(store.pop(name_key))
                promoted = existing.model_copy(
                    update={
                        "item_id": record.item_id,
                        "item_name": record.item_name,
                        "pinned": _record_is_pinned(existing),
                    }
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
        removed = _remove_aliases(store, item_name=request.item_name, item_id=request.item_id)
        if removed:
            save_offer_metadata_store(store)
    return {"ok": True, "removed": removed}


@router.patch("/items/{item_id}/name")
async def rename_shopping_item(item_id: str, request: RenameShoppingItemRequest) -> dict[str, object]:
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

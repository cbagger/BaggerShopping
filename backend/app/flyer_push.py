from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import APIRouter
from pydantic import BaseModel, Field

# Discovery must use the raw deterministic provider objects. Otherwise Luna's
# own cached overlay could change variants/prices and accidentally look like a
# new flyer version, creating a self-triggering enrichment loop.
from . import _original_fetch_all_publications as fetch_all_publications
from .flyer_adapters import RETAILER_ORDER
from .flyer_readiness import (
    observe_publications,
    publication_fingerprint,
    publication_is_ready,
    readiness_revision,
    ready_publication_records,
)
from .households import current_household

router = APIRouter(prefix="/api/mobile/v1/flyer-notifications", tags=["flyer-notifications"])
STORE_LOCK = asyncio.Lock()


def _store_path() -> Path:
    return Path(os.getenv("FLYER_PUSH_STORE_PATH", "/data/flyer-push.json"))


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    temporary.replace(path)


class DevicePreferences(BaseModel):
    device_id: str = Field(min_length=16, max_length=100)
    device_token: str = Field(pattern=r"^[0-9a-fA-F]{64,200}$")
    retailers: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    environment: str = Field(default="production", pattern=r"^(production|sandbox)$")


class DeviceRemove(BaseModel):
    device_id: str = Field(min_length=16, max_length=100)


@router.get("/retailers")
async def notification_retailers() -> dict[str, Any]:
    return {"ok": True, "retailers": list(RETAILER_ORDER)}


@router.put("/device")
async def put_device(request: DevicePreferences) -> dict[str, Any]:
    selected = [name for name in RETAILER_ORDER if name in set(request.retailers)]
    async with STORE_LOCK:
        store = _load()
        devices = store.setdefault("devices", {})
        devices[request.device_id] = {
            "household_id": current_household().household_id,
            "device_token": request.device_token.lower(),
            "retailers": selected,
            "enabled": request.enabled,
            "environment": request.environment,
            "updated_at": int(time.time()),
        }
        _save(store)
    return {"ok": True, "retailers": selected, "enabled": request.enabled}


@router.post("/device/remove")
async def remove_device(request: DeviceRemove) -> dict[str, Any]:
    async with STORE_LOCK:
        store = _load()
        removed = store.setdefault("devices", {}).pop(request.device_id, None) is not None
        _save(store)
    return {"ok": True, "removed": removed}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _provider_token() -> str:
    key_id = os.environ["APNS_KEY_ID"]
    team_id = os.environ["APNS_TEAM_ID"]
    key = serialization.load_pem_private_key(Path(os.environ["APNS_PRIVATE_KEY_PATH"]).read_bytes(), password=None)
    header = _b64(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": team_id, "iat": int(time.time())}, separators=(",", ":")).encode())
    der = key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


async def _send(
    device_token: str,
    environment: str,
    title: str,
    body: str,
    publication_id: str,
    retailer: str,
) -> bool:
    host = "api.sandbox.push.apple.com" if environment == "sandbox" else "api.push.apple.com"
    payload = {
        "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        "route": "flyer",
        "publication_id": publication_id,
        "retailer": retailer,
    }
    headers = {
        "authorization": f"bearer {_provider_token()}",
        "apns-topic": os.getenv("APNS_BUNDLE_ID", "dk.chewbagger.BaggerShopping"),
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        response = await client.post(f"https://{host}/3/device/{device_token}", headers=headers, json=payload)
    return response.status_code == 200


def _notification_name(publication: Any) -> str:
    title = str(getattr(publication, "title", "") or "").strip()
    retailer = str(getattr(publication, "retailer", "") or "").strip()
    return title if retailer.casefold() in title.casefold() else f"{retailer} {title}".strip()


def _version_key(publication: Any) -> str:
    return f"{publication.id}:{publication_fingerprint(publication)}"


def _record_version_key(record: dict[str, Any]) -> str:
    return f"{record.get('publication_id')}:{record.get('fingerprint')}"


def _install_mobile_cache_readiness_guard() -> None:
    """Invalidate the Mobile API flyer cache whenever readiness changes.

    `mobile_main` imports `mobile_offers` before this module, so in that process
    the module is already available and can be wrapped without a circular
    import. The standalone flyer-push worker never imports mobile_offers, so the
    hook is a no-op there. Endpoint functions resolve `_publications` from their
    module globals at call time, making this safe for the existing router.
    """
    mobile = sys.modules.get(f"{__package__}.mobile_offers")
    if mobile is None:
        return
    original = getattr(mobile, "_publications", None)
    if original is None or getattr(original, "_readiness_guard_installed", False):
        return

    state = {"revision": readiness_revision()}

    async def guarded_publications():
        revision = readiness_revision()
        if revision != state["revision"]:
            lock = getattr(mobile, "_publication_lock")
            async with lock:
                mobile._publications_cache = []
                mobile._publication_cache = None
                mobile._publication_cache_time = 0.0
            state["revision"] = revision
        return await original()

    guarded_publications._readiness_guard_installed = True
    mobile._publications = guarded_publications


_install_mobile_cache_readiness_guard()


async def _deliver_ready_records(records: list[dict[str, Any]]) -> dict[str, int]:
    """Deliver ready flyer versions using only local readiness metadata."""
    async with STORE_LOCK:
        store = _load()
        if not store.get("initialized"):
            return {"ready": 0, "sent": 0, "failed": 0}
        seen_versions = dict(store.get("seen_publication_fingerprints", {}))
        devices = dict(store.get("devices", {}))

    ready_unseen = [
        row for row in records
        if row.get("publication_id")
        and row.get("fingerprint")
        and seen_versions.get(str(row["publication_id"])) != str(row["fingerprint"])
    ]

    sent = failed = 0
    completed_versions: dict[str, str] = {}

    for row in ready_unseen:
        publication_id = str(row["publication_id"])
        retailer = str(row.get("retailer") or "")
        title = str(row.get("title") or "")
        fingerprint = str(row["fingerprint"])
        version_key = _record_version_key(row)
        notification_name = title if retailer.casefold() in title.casefold() else f"{retailer} {title}".strip()
        targets = 0
        publication_failed = False

        for device_id, device in devices.items():
            if not device.get("enabled") or retailer not in device.get("retailers", []):
                continue
            targets += 1
            delivered_versions = set(device.get("delivered_publication_versions", []))
            if version_key in delivered_versions:
                continue

            ok = await _send(
                device["device_token"],
                device.get("environment", "production"),
                "Ny tilbudsavis",
                f"{notification_name} er nu tilgængelig",
                publication_id,
                retailer,
            )
            sent += int(ok)
            failed += int(not ok)
            publication_failed = publication_failed or not ok

            if ok:
                async with STORE_LOCK:
                    latest = _load()
                    record = latest.setdefault("devices", {}).get(device_id)
                    if record is not None:
                        history = list(dict.fromkeys([
                            *record.get("delivered_publication_versions", []),
                            version_key,
                        ]))[-300:]
                        record["delivered_publication_versions"] = history
                        _save(latest)

        if targets == 0 or not publication_failed:
            completed_versions[publication_id] = fingerprint

    if completed_versions:
        async with STORE_LOCK:
            store = _load()
            seen = set(store.get("seen_publications", []))
            versions = store.setdefault("seen_publication_fingerprints", {})
            for publication_id, fingerprint in completed_versions.items():
                seen.add(publication_id)
                versions[publication_id] = fingerprint
            store["seen_publications"] = sorted(seen)
            store["last_ready_delivery_at"] = int(time.time())
            _save(store)

    return {"ready": len(ready_unseen), "sent": sent, "failed": failed}


async def deliver_ready_notifications() -> dict[str, int]:
    """Local, provider-free delivery path used after Luna marks a flyer ready."""
    return await _deliver_ready_records(ready_publication_records())


async def check_and_send() -> dict[str, int]:
    publications = [item for item in await fetch_all_publications() if item.status != "expired"]
    current = {item.id: item for item in publications}

    # Migrate the existing push history into version-aware tracking. On the very
    # first install there is no previous history, so all current flyers are the
    # baseline and must not trigger pushes/Luna. On an upgrade, readiness uses
    # the existing seen IDs as the safe baseline and queues anything else.
    async with STORE_LOCK:
        store = _load()
        initialized = bool(store.get("initialized"))
        previous_ids = set(store.get("seen_publications", []))
        seen_versions = dict(store.get("seen_publication_fingerprints", {}))

        if not initialized:
            store["initialized"] = True
            store["seen_publications"] = sorted(current)
            store["seen_publication_fingerprints"] = {
                publication.id: publication_fingerprint(publication)
                for publication in publications
            }
            store["last_check_at"] = int(time.time())
            _save(store)
            observe_publications(publications, bootstrap_ready_ids=None)
            return {"new": 0, "sent": 0, "failed": 0}

        # Existing installations predate version fingerprints. Seed only IDs
        # already known by the old notification worker; an actually new flyer
        # present during deployment therefore remains pending instead of being
        # accidentally published.
        if not seen_versions and previous_ids:
            seen_versions = {
                publication.id: publication_fingerprint(publication)
                for publication in publications
                if publication.id in previous_ids
            }
            store["seen_publication_fingerprints"] = dict(seen_versions)
            _save(store)

    observe_publications(publications, bootstrap_ready_ids=previous_ids)

    async with STORE_LOCK:
        store = _load()
        seen_versions = dict(store.get("seen_publication_fingerprints", {}))

    unseen = [
        publication
        for publication in current.values()
        if seen_versions.get(publication.id) != publication_fingerprint(publication)
    ]

    # Detection only queues processing versions. Delivery is driven by the
    # shared local readiness store, so no second provider fetch is needed when
    # Luna later flips the version to ready.
    delivery = await deliver_ready_notifications()

    async with STORE_LOCK:
        store = _load()
        store["last_check_at"] = int(time.time())
        _save(store)

    return {
        "new": len(unseen),
        "sent": delivery["sent"],
        "failed": delivery["failed"],
    }


async def worker() -> None:
    provider_interval = max(300, int(os.getenv("FLYER_PUSH_INTERVAL_SECONDS", "3600")))
    ready_interval = max(5, int(os.getenv("FLYER_READY_POLL_SECONDS", "10")))
    next_provider_check = 0.0

    while True:
        try:
            now = time.monotonic()
            if now >= next_provider_check:
                result = await check_and_send()
                next_provider_check = time.monotonic() + provider_interval
                print({"flyer_push": result}, flush=True)
            else:
                delivery = await deliver_ready_notifications()
                if delivery["ready"] or delivery["sent"] or delivery["failed"]:
                    print({"flyer_ready_push": delivery}, flush=True)
        except Exception as exc:
            print({"flyer_push_error": str(exc)}, flush=True)
        await asyncio.sleep(ready_interval)


if __name__ == "__main__":
    asyncio.run(worker())

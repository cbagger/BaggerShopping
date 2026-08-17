from __future__ import annotations

import asyncio
import base64
import json
import os
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
    title = publication.title.strip()
    return title if publication.retailer.casefold() in title.casefold() else f"{publication.retailer} {title}".strip()


def _version_key(publication: Any) -> str:
    return f"{publication.id}:{publication_fingerprint(publication)}"


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
        devices = dict(store.get("devices", {}))

    unseen = [
        publication
        for publication in current.values()
        if seen_versions.get(publication.id) != publication_fingerprint(publication)
    ]

    # Readiness is the publication gate. A flyer can be detected for hours
    # without being pushed; it becomes eligible only after Luna marks the exact
    # provider version ready.
    ready_unseen = [publication for publication in unseen if publication_is_ready(publication)]

    sent = failed = 0
    completed_versions: dict[str, str] = {}

    for publication in ready_unseen:
        fingerprint = publication_fingerprint(publication)
        version_key = _version_key(publication)
        targets = 0
        publication_failed = False

        for device_id, device in devices.items():
            if not device.get("enabled") or publication.retailer not in device.get("retailers", []):
                continue
            targets += 1
            delivered_versions = set(device.get("delivered_publication_versions", []))
            if version_key in delivered_versions:
                continue

            ok = await _send(
                device["device_token"],
                device.get("environment", "production"),
                "Ny tilbudsavis",
                f"{_notification_name(publication)} er nu tilgængelig",
                publication.id,
                publication.retailer,
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
            completed_versions[publication.id] = fingerprint

    async with STORE_LOCK:
        store = _load()
        seen = set(store.get("seen_publications", []))
        versions = store.setdefault("seen_publication_fingerprints", {})
        for publication_id, fingerprint in completed_versions.items():
            seen.add(publication_id)
            versions[publication_id] = fingerprint
        store["seen_publications"] = sorted(seen)
        store["last_check_at"] = int(time.time())
        _save(store)

    return {"new": len(unseen), "sent": sent, "failed": failed}


async def worker() -> None:
    interval = max(300, int(os.getenv("FLYER_PUSH_INTERVAL_SECONDS", "3600")))
    while True:
        try:
            print({"flyer_push": await check_and_send()}, flush=True)
        except Exception as exc:
            print({"flyer_push_error": str(exc)}, flush=True)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(worker())

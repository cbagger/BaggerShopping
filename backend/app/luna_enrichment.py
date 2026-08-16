from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .member_pricing import detect_member_pricing, has_membership_signal
from .meny_flyer import Offer, Publication

CONFIG_PATH = Path(os.getenv("LUNA_CONFIG_PATH", "/data/luna-config.json"))
STORE_PATH = Path(os.getenv("LUNA_STORE_PATH", "/data/luna-enrichment-store.json"))
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "apply_results": True,
    "model": "gpt-5.6-luna",
    "monthly_budget_dkk": 25.0,
    "max_requests_per_month": 250,
    "max_requests_per_scan": 20,
    "scan_interval_seconds": 3600,
    "min_apply_confidence": 0.96,
    # Conservative, configurable accounting. If OpenAI's live price is lower,
    # Kurv simply reaches the configured budget later than this estimate says.
    "input_usd_per_million": 1.0,
    "output_usd_per_million": 6.0,
    "usd_to_dkk": 7.0,
}

_store_lock = threading.RLock()
_store_cache: dict[str, Any] | None = None
_store_signature: tuple[int, int] | None = None
_config_cache: dict[str, Any] | None = None
_config_signature: tuple[int, int] | None = None
MEMBERSHIP_FEE_RE = re.compile(
    r"\b(?:medlemskab|medlemsgebyr|engangsbeløb|oprettelsesgebyr)\b", re.IGNORECASE
)
VARIANT_HINT_RE = re.compile(r"\b(?:eller|frit valg|flere varianter|vælg mellem)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewDecision:
    review: bool
    reasons: tuple[str, ...]
    requested_fields: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class Candidate:
    fingerprint: str
    publication: Publication
    offer: Offer
    decision: ReviewDecision


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(fallback)
    except (OSError, json.JSONDecodeError):
        return dict(fallback)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def load_config() -> dict[str, Any]:
    global _config_cache, _config_signature
    signature = _signature(CONFIG_PATH)
    with _store_lock:
        if _config_cache is not None and signature == _config_signature:
            return dict(_config_cache)
        merged = {**DEFAULT_CONFIG, **_read_json(CONFIG_PATH, {})}
        _config_cache = merged
        _config_signature = signature
        return dict(merged)


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    global _config_cache, _config_signature
    with _store_lock:
        value = {**load_config(), **updates}
        _write_json(CONFIG_PATH, value)
        _config_cache = value
        _config_signature = _signature(CONFIG_PATH)
        return dict(value)


def _empty_store() -> dict[str, Any]:
    return {"records": {}, "pricing_index": {}, "usage": {}, "events": []}


def load_store() -> dict[str, Any]:
    global _store_cache, _store_signature
    signature = _signature(STORE_PATH)
    with _store_lock:
        if _store_cache is not None and signature == _store_signature:
            return json.loads(json.dumps(_store_cache))
        value = _read_json(STORE_PATH, _empty_store())
        for key, default in _empty_store().items():
            value.setdefault(key, default)
        _store_cache = value
        _store_signature = signature
        return json.loads(json.dumps(value))


def save_store(value: dict[str, Any]) -> None:
    global _store_cache, _store_signature
    with _store_lock:
        value.setdefault("pricing_index", {})
        value["events"] = list(value.get("events", []))[-500:]
        _write_json(STORE_PATH, value)
        _store_cache = json.loads(json.dumps(value))
        _store_signature = _signature(STORE_PATH)


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _compact(value: str) -> str:
    return " ".join((value or "").replace("\u00ad", "").split())


def pricing_signature(
    *, retailer: str, price: float | None, normal_price: float | None,
    text: str, unit_price: str | None,
) -> str:
    payload = {
        "retailer": _compact(retailer).casefold(),
        "price": price,
        "normal_price": normal_price,
        "text": _compact(text),
        "unit_price": _compact(unit_price or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def offer_pricing_signature(offer: Offer) -> str:
    return pricing_signature(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=" ".join(filter(None, (offer.product_name, offer.raw_text))),
        unit_price=offer.unit_price,
    )


def offer_fingerprint(offer: Offer) -> str:
    # Image URLs are deliberately excluded because provider-signed URLs rotate.
    payload = {
        "publication": offer.publication_id, "offer": offer.id,
        "retailer": offer.retailer, "page": offer.page_number,
        "product": offer.product_name, "price": offer.price,
        "normal_price": offer.normal_price, "text": offer.raw_text,
        "box": [offer.hotspot_x, offer.hotspot_y, offer.hotspot_width, offer.hotspot_height],
        "variants": [variant.name for variant in offer.variants],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def member_pricing_override(
    *, retailer: str, price: float | None, normal_price: float | None,
    text: str, unit_price: str | None,
) -> dict[str, Any] | None:
    """Read a cached correction only; this function can never call OpenAI.

    Turning Luna off therefore immediately restores the deterministic result.
    Provider/source data is never overwritten or migrated by this layer.
    """
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return None
    store = load_store()
    signature = pricing_signature(
        retailer=retailer, price=price, normal_price=normal_price,
        text=text, unit_price=unit_price,
    )
    fingerprint = store.get("pricing_index", {}).get(signature)
    row = store.get("records", {}).get(fingerprint) if fingerprint else None
    if not isinstance(row, dict) or row.get("status") != "completed":
        return None
    facts = row.get("facts")
    if not isinstance(facts, dict) or not facts.get("same_offer"):
        return None
    confidence = float(facts.get("pricing_confidence") or 0)
    if confidence < float(config.get("min_apply_confidence", 0.96)):
        return None
    return {
        "authoritative": True,
        "ordinary_price": facts.get("ordinary_price"),
        "member_price": facts.get("member_price"),
        "member_program": facts.get("member_program"),
        "member_app": facts.get("member_app"),
        "requires_activation": bool(facts.get("requires_activation")),
        "pricing_confidence": confidence,
        "fingerprint": fingerprint,
    }


def review_decision(offer: Offer) -> ReviewDecision:
    reasons: list[str] = []
    fields: set[str] = set()
    priority = 0
    text = " ".join(filter(None, (offer.product_name, offer.raw_text)))
    pricing = detect_member_pricing(
        retailer=offer.retailer, price=offer.price, normal_price=offer.normal_price,
        text=text, unit_price=offer.unit_price,
    )
    if has_membership_signal(text):
        fields.update({"pricing", "membership"})
        if pricing is None:
            reasons.append("member-signal-without-safe-price")
            priority += 80
        else:
            if getattr(pricing, "confidence", 1.0) < 0.96 or pricing.source.startswith("page-context"):
                reasons.append("member-price-needs-visual-verification")
                priority += 75
            if pricing.ordinary_price is None:
                reasons.append("member-price-missing-ordinary-price")
                priority += 55
        if MEMBERSHIP_FEE_RE.search(text):
            reasons.append("membership-fee-near-product-prices")
            priority += 35
    if offer.price is None:
        reasons.append("missing-primary-price")
        fields.add("pricing")
        priority += 45
    if offer.normal_price is not None and offer.price is not None:
        if offer.price >= 5 and offer.normal_price > offer.price * 4 and offer.normal_price - offer.price > 60:
            reasons.append("implausible-provider-reference-price")
            fields.add("pricing")
            priority += 60
    if VARIANT_HINT_RE.search(text) and len(offer.variants) <= 1 and offer.variant_confidence < 0.80:
        reasons.append("ambiguous-variants")
        fields.add("variants")
        priority += 25
    if offer.quality_score < 0.50:
        reasons.append("low-provider-quality")
        fields.update({"identity", "pricing"})
        priority += 20
    return ReviewDecision(
        bool(reasons), tuple(dict.fromkeys(reasons)), tuple(sorted(fields)), priority
    )


def collect_candidates(publications: Iterable[Publication]) -> list[Candidate]:
    store = load_store()
    records = store.get("records", {})
    result: list[Candidate] = []
    for publication in publications:
        if publication.status == "expired":
            continue
        for offer in publication.structured_offers:
            decision = review_decision(offer)
            if not decision.review:
                continue
            fingerprint = offer_fingerprint(offer)
            existing = records.get(fingerprint)
            if isinstance(existing, dict) and existing.get("status") in {"completed", "no-change", "pending"}:
                continue
            result.append(Candidate(fingerprint, publication, offer, decision))
    return sorted(
        result,
        key=lambda value: (-value.decision.priority, value.offer.retailer, value.offer.page_number or 0),
    )


def _page_image(publication: Publication, offer: Offer) -> str | None:
    if offer.page_number is None or not (0 < offer.page_number <= len(publication.page_image_urls)):
        return None
    return publication.page_image_urls[offer.page_number - 1]


def image_input(candidate: Candidate) -> tuple[str | None, str]:
    page = _page_image(candidate.publication, candidate.offer)
    # Tjek already supplies exact advert crops: cheapest and least ambiguous.
    if (
        candidate.offer.quality_source == "tjek-catalog"
        and candidate.offer.image_url
        and candidate.offer.image_url != page
    ):
        return candidate.offer.image_url, "low"
    return page or candidate.offer.image_url, "high"


def _schema() -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "same_offer": {"type": "boolean"}, "product_name": nullable_string,
            "brand": nullable_string, "ordinary_price": nullable_number,
            "member_price": nullable_number, "member_program": nullable_string,
            "member_app": nullable_string, "requires_activation": {"type": "boolean"},
            "before_price": nullable_number, "unit_price": nullable_string,
            "variants": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
            "identity_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "pricing_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "variant_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "same_offer", "product_name", "brand", "ordinary_price", "member_price",
            "member_program", "member_app", "requires_activation", "before_price",
            "unit_price", "variants", "identity_confidence", "pricing_confidence",
            "variant_confidence",
        ],
    }


def _prompt(candidate: Candidate) -> str:
    offer = candidate.offer
    box = None
    if None not in (offer.hotspot_x, offer.hotspot_y, offer.hotspot_width, offer.hotspot_height):
        box = {"x": offer.hotspot_x, "y": offer.hotspot_y,
               "width": offer.hotspot_width, "height": offer.hotspot_height}
    context = {
        "retailer": offer.retailer, "publication": offer.publication_title,
        "target_product": offer.product_name, "provider_price": offer.price,
        "provider_reference_price": offer.normal_price,
        "provider_unit_price": offer.unit_price,
        "provider_variants": [variant.name for variant in offer.variants],
        "provider_text": offer.raw_text[:1800], "target_hotspot_normalized": box,
        "review_reasons": list(candidate.decision.reasons),
        "requested_fields": list(candidate.decision.requested_fields),
    }
    return (
        "You are Kurv's flyer verification layer. Inspect ONLY the advert for the target product. "
        "Do not borrow prices, membership badges or legal text from neighbouring adverts. "
        "ordinary_price is what a non-member pays for THIS product in the same campaign; it is NOT "
        "kg/l/100g unit price, an old before-price, membership fee, deposit, package count, or another advert's price. "
        "member_price is only a price explicitly tied to membership/app/club for THIS product. "
        "requires_activation is true only when the advert explicitly says a coupon/offer must be activated/clipped; "
        "app membership alone is false. If a value cannot be read confidently, return null. "
        "Preserve the advertised membership programme name. The normalized hotspot identifies the target area on a full-page image.\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def _request_body(candidate: Candidate, config: dict[str, Any]) -> dict[str, Any]:
    image_url, detail = image_input(candidate)
    content: list[dict[str, Any]] = [{"type": "input_text", "text": _prompt(candidate)}]
    if image_url:
        content.append({"type": "input_image", "image_url": image_url, "detail": detail})
    return {
        "model": str(config.get("model") or "gpt-5.6-luna"),
        "input": [{"role": "user", "content": content}],
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "low", "format": {
            "type": "json_schema", "name": "kurv_offer_facts",
            "strict": True, "schema": _schema(),
        }},
        "max_output_tokens": 420,
    }


def _output_text(body: dict[str, Any]) -> str | None:
    for item in body.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    return None


def _validated_facts(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = {
        "same_offer", "product_name", "brand", "ordinary_price", "member_price",
        "member_program", "member_app", "requires_activation", "before_price",
        "unit_price", "variants", "identity_confidence", "pricing_confidence", "variant_confidence",
    }
    if not required.issubset(value):
        return None
    for key in ("identity_confidence", "pricing_confidence", "variant_confidence"):
        if not isinstance(value.get(key), (int, float)) or not 0 <= float(value[key]) <= 1:
            return None
    if not isinstance(value.get("variants"), list) or not all(isinstance(item, str) for item in value["variants"]):
        return None
    return value


def _usage_cost_dkk(usage: dict[str, Any], config: dict[str, Any]) -> float:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    usd = (
        input_tokens / 1_000_000 * float(config.get("input_usd_per_million", 1.0))
        + output_tokens / 1_000_000 * float(config.get("output_usd_per_million", 6.0))
    )
    return round(usd * float(config.get("usd_to_dkk", 7.0)), 6)


def usage_status(config: dict[str, Any] | None = None, store: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    store = store or load_store()
    usage = store.get("usage", {}).get(month_key(), {})
    spent = float(usage.get("estimated_cost_dkk") or 0.0)
    requests = int(usage.get("requests") or 0)
    budget = float(config.get("monthly_budget_dkk", 25.0))
    return {
        "month": month_key(), "requests": requests,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "estimated_cost_dkk": round(spent, 4), "budget_dkk": budget,
        "remaining_dkk": round(max(0.0, budget - spent), 4),
        "request_limit": int(config.get("max_requests_per_month", 250)),
    }


def budget_allows_request(config: dict[str, Any] | None = None, store: dict[str, Any] | None = None) -> bool:
    config = config or load_config()
    status = usage_status(config, store)
    return status["estimated_cost_dkk"] < status["budget_dkk"] and status["requests"] < status["request_limit"]


def _record_usage(store: dict[str, Any], usage: dict[str, Any], config: dict[str, Any]) -> None:
    row = store.setdefault("usage", {}).setdefault(month_key(), {})
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    row["requests"] = int(row.get("requests") or 0) + 1
    row["input_tokens"] = int(row.get("input_tokens") or 0) + input_tokens
    row["output_tokens"] = int(row.get("output_tokens") or 0) + output_tokens
    row["estimated_cost_dkk"] = round(
        float(row.get("estimated_cost_dkk") or 0) + _usage_cost_dkk(usage, config), 6
    )
    row["updated_at"] = int(time.time())


async def analyze_candidate(candidate: Candidate, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "disabled"}
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing-api-key"}
    store = load_store()
    if not budget_allows_request(config, store):
        return {"status": "budget-exhausted"}

    signature = offer_pricing_signature(candidate.offer)
    record = {
        "status": "pending", "retailer": candidate.offer.retailer,
        "publication_id": candidate.offer.publication_id, "offer_id": candidate.offer.id,
        "product_name": candidate.offer.product_name, "pricing_signature": signature,
        "reasons": list(candidate.decision.reasons),
        "requested_fields": list(candidate.decision.requested_fields),
        "created_at": int(time.time()),
    }
    store.setdefault("records", {})[candidate.fingerprint] = record
    store.setdefault("pricing_index", {})[signature] = candidate.fingerprint
    save_store(store)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=90.0, follow_redirects=True)
    try:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_request_body(candidate, config),
        )
        response.raise_for_status()
        body = response.json()
        text = _output_text(body)
        facts = _validated_facts(json.loads(text)) if text else None
        store = load_store()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        _record_usage(store, usage, config)
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)
        store.setdefault("pricing_index", {})[signature] = candidate.fingerprint
        if facts is None:
            row.update({"status": "failed", "error": "invalid-structured-output", "updated_at": int(time.time())})
        else:
            threshold = float(config.get("min_apply_confidence", 0.96))
            useful = bool(facts.get("same_offer")) and (
                float(facts.get("pricing_confidence") or 0) >= threshold
                or float(facts.get("variant_confidence") or 0) >= threshold
            )
            row.update({
                "status": "completed" if useful else "no-change", "facts": facts,
                "model": body.get("model") or config.get("model"),
                "response_id": body.get("id"), "usage": usage, "updated_at": int(time.time()),
            })
        store.setdefault("events", []).append({
            "at": int(time.time()), "event": "analysis", "fingerprint": candidate.fingerprint,
            "status": row.get("status"), "retailer": candidate.offer.retailer,
        })
        save_store(store)
        return dict(row)
    except Exception as exc:
        store = load_store()
        row = store.setdefault("records", {}).setdefault(candidate.fingerprint, record)
        row.update({"status": "failed", "error": str(exc)[:500], "updated_at": int(time.time())})
        store.setdefault("pricing_index", {})[signature] = candidate.fingerprint
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


def status_payload() -> dict[str, Any]:
    config = load_config()
    store = load_store()
    counts: dict[str, int] = {}
    for row in store.get("records", {}).values():
        if isinstance(row, dict):
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
    return {
        "enabled": bool(config.get("enabled")),
        "apply_results": bool(config.get("apply_results")),
        "model": config.get("model"),
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "usage": usage_status(config, store), "records": counts,
        "pricing_index_count": len(store.get("pricing_index", {})),
        "scan_interval_seconds": int(config.get("scan_interval_seconds", 3600)),
    }

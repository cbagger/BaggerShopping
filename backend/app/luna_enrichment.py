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
from .meny_flyer import Offer, OfferVariant, Publication

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
    "input_usd_per_million": 1.0,
    "output_usd_per_million": 6.0,
    "usd_to_dkk": 7.0,
}

_store_lock = threading.RLock()
_store_cache: dict[str, Any] | None = None
_store_signature: tuple[int, int] | None = None
_config_cache: dict[str, Any] | None = None
_config_signature: tuple[int, int] | None = None

MEMBERSHIP_FEE_RE = re.compile(r"\b(?:medlemskab|medlemsgebyr|engangsbeløb|oprettelsesgebyr)\b", re.IGNORECASE)
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
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def load_config() -> dict[str, Any]:
    global _config_cache, _config_signature
    signature = _signature(CONFIG_PATH)
    with _store_lock:
        if _config_cache is not None and signature == _config_signature:
            return dict(_config_cache)
        raw = _read_json(CONFIG_PATH, {})
        merged = {**DEFAULT_CONFIG, **raw}
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
    return {"records": {}, "usage": {}, "events": []}


def load_store() -> dict[str, Any]:
    global _store_cache, _store_signature
    signature = _signature(STORE_PATH)
    with _store_lock:
        if _store_cache is not None and signature == _store_signature:
            return json.loads(json.dumps(_store_cache))
        value = _read_json(STORE_PATH, _empty_store())
        value.setdefault("records", {})
        value.setdefault("usage", {})
        value.setdefault("events", [])
        _store_cache = value
        _store_signature = signature
        return json.loads(json.dumps(value))


def save_store(value: dict[str, Any]) -> None:
    global _store_cache, _store_signature
    with _store_lock:
        value["events"] = list(value.get("events", []))[-500:]
        _write_json(STORE_PATH, value)
        _store_cache = json.loads(json.dumps(value))
        _store_signature = _signature(STORE_PATH)


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def offer_fingerprint(offer: Offer) -> str:
    payload = {
        "publication": offer.publication_id,
        "offer": offer.id,
        "retailer": offer.retailer,
        "page": offer.page_number,
        "product": offer.product_name,
        "price": offer.price,
        "normal_price": offer.normal_price,
        "text": offer.raw_text,
        "box": [offer.hotspot_x, offer.hotspot_y, offer.hotspot_width, offer.hotspot_height],
        "variants": [variant.name for variant in offer.variants],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def deterministic_member_pricing(offer: Offer):
    return detect_member_pricing(
        retailer=offer.retailer,
        price=offer.price,
        normal_price=offer.normal_price,
        text=" ".join(filter(None, (offer.product_name, offer.raw_text))),
        unit_price=offer.unit_price,
    )


def review_decision(offer: Offer) -> ReviewDecision:
    reasons: list[str] = []
    fields: set[str] = set()
    priority = 0
    text = " ".join(filter(None, (offer.product_name, offer.raw_text)))
    pricing = deterministic_member_pricing(offer)
    member_signal = has_membership_signal(text)

    if member_signal:
        fields.update({"pricing", "membership"})
        if pricing is None:
            reasons.append("member-signal-without-safe-price")
            priority += 80
        else:
            if pricing.confidence < 0.96 or pricing.source.startswith("page-context"):
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

    return ReviewDecision(bool(reasons), tuple(dict.fromkeys(reasons)), tuple(sorted(fields)), priority)


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
    return sorted(result, key=lambda value: (-value.decision.priority, value.offer.retailer, value.offer.page_number or 0))


def _page_image(publication: Publication, offer: Offer) -> str | None:
    if offer.page_number is None or not (0 < offer.page_number <= len(publication.page_image_urls)):
        return None
    return publication.page_image_urls[offer.page_number - 1]


def image_input(candidate: Candidate) -> tuple[str | None, str]:
    offer = candidate.offer
    page = _page_image(candidate.publication, offer)
    if offer.quality_source == "tjek-catalog" and offer.image_url and offer.image_url != page:
        return offer.image_url, "low"
    return page or offer.image_url, "high"


def _schema() -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "same_offer": {"type": "boolean"},
            "product_name": nullable_string,
            "brand": nullable_string,
            "ordinary_price": nullable_number,
            "member_price": nullable_number,
            "member_program": nullable_string,
            "member_app": nullable_string,
            "requires_activation": {"type": "boolean"},
            "before_price": nullable_number,
            "unit_price": nullable_string,
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
        box = {
            "x": offer.hotspot_x,
            "y": offer.hotspot_y,
            "width": offer.hotspot_width,
            "height": offer.hotspot_height,
        }
    context = {
        "retailer": offer.retailer,
        "publication": offer.publication_title,
        "target_product": offer.product_name,
        "provider_price": offer.price,
        "provider_reference_price": offer.normal_price,
        "provider_unit_price": offer.unit_price,
        "provider_variants": [variant.name for variant in offer.variants],
        "provider_text": offer.raw_text[:1800],
        "target_hotspot_normalized": box,
        "review_reasons": list(candidate.decision.reasons),
        "requested_fields": list(candidate.decision.requested_fields),
    }
    return (
        "You are Kurv's flyer verification layer. Inspect ONLY the advert for the target product. "
        "Do not borrow prices, membership badges or legal text from neighbouring adverts. "
        "ordinary_price means the price a non-member pays for THIS product during the same campaign; "
        "it is NOT kg/l/100g unit price, an old before-price, membership fee, deposit, package count, or another advert's price. "
        "member_price is only a price explicitly tied to membership/app/club for THIS product. "
        "requires_activation is true only when the advert says a coupon/offer must be activated/clipped, not merely because an app or membership is required. "
        "If a value cannot be read confidently, return null. Preserve the advertised membership programme name. "
        "The normalized hotspot, when present, identifies the target area on a full-page image.\n\n"
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
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "kurv_offer_facts",
                "strict": True,
                "schema": _schema(),
            },
        },
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
    return {
        "month": month_key(),
        "requests": requests,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "estimated_cost_dkk": round(spent, 4),
        "budget_dkk": float(config.get("monthly_budget_dkk", 25.0)),
        "remaining_dkk": round(max(0.0, float(config.get("monthly_budget_dkk", 25.0)) - spent), 4),
        "request_limit": int(config.get("max_requests_per_month", 250)),
    }


def budget_allows_request(config: dict[str, Any] | None = None, store: dict[str, Any] | None = None) -> bool:
    config = config or load_config()
    status = usage_status(config, store)
    return status["estimated_cost_dkk"] < status["budget_dkk"] and status["requests"] < status["request_limit"]


def _record_usage(store: dict[str, Any], usage: dict[str, Any], config: dict[str, Any]) -> None:
    key = month_key()
    months = store.setdefault("usage", {})
    row = months.setdefault(key, {})
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    row["requests"] = int(row.get("requests") or 0) + 1
    row["input_tokens"] = int(row.get("input_tokens") or 0) + input_tokens
    row["output_tokens"] = int(row.get("output_tokens") or 0) + output_tokens
    row["estimated_cost_dkk"] = round(float(row.get("estimated_cost_dkk") or 0) + _usage_cost_dkk(usage, config), 6)
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

    record = {
        "status": "pending",
        "retailer": candidate.offer.retailer,
        "publication_id": candidate.offer.publication_id,
        "offer_id": candidate.offer.id,
        "product_name": candidate.offer.product_name,
        "reasons": list(candidate.decision.reasons),
        "requested_fields": list(candidate.decision.requested_fields),
        "created_at": int(time.time()),
    }
    store.setdefault("records", {})[candidate.fingerprint] = record
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
        if facts is None:
            row.update({"status": "failed", "error": "invalid-structured-output", "updated_at": int(time.time())})
        else:
            threshold = float(config.get("min_apply_confidence", 0.96))
            useful = bool(facts.get("same_offer")) and (
                float(facts.get("pricing_confidence") or 0) >= threshold
                or float(facts.get("variant_confidence") or 0) >= threshold
            )
            row.update({
                "status": "completed" if useful else "no-change",
                "facts": facts,
                "model": body.get("model") or config.get("model"),
                "response_id": body.get("id"),
                "usage": usage,
                "updated_at": int(time.time()),
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
        save_store(store)
        return dict(row)
    finally:
        if owns_client:
            await client.aclose()


def _copy_with_facts(offer: Offer, facts: dict[str, Any], config: dict[str, Any]) -> Offer:
    if not facts.get("same_offer"):
        return offer
    threshold = float(config.get("min_apply_confidence", 0.96))
    updates: dict[str, Any] = {"ai_enrichment": facts}

    identity_confidence = float(facts.get("identity_confidence") or 0)
    pricing_confidence = float(facts.get("pricing_confidence") or 0)
    variant_confidence = float(facts.get("variant_confidence") or 0)

    if pricing_confidence >= threshold and facts.get("ordinary_price") is not None:
        updates["price"] = float(facts["ordinary_price"])
    if identity_confidence >= 0.99 and offer.quality_score < 0.50 and facts.get("product_name"):
        updates["product_name"] = str(facts["product_name"]).strip()
    if identity_confidence >= threshold and not offer.brand and facts.get("brand"):
        updates["brand"] = str(facts["brand"]).strip()
    if variant_confidence >= 0.99 and offer.variant_confidence < 0.65:
        names = [str(value).strip() for value in facts.get("variants", []) if str(value).strip()]
        if names:
            updates["variants"] = [
                OfferVariant(
                    id=hashlib.sha256(f"{offer.id}|luna|{name}".encode()).hexdigest()[:20],
                    name=name,
                )
                for name in dict.fromkeys(names)
            ]
            updates["variant_confidence"] = variant_confidence
            updates["quality_signals"] = list(dict.fromkeys([*offer.quality_signals, "luna-verified-variants"]))

    return offer.model_copy(update=updates)


def apply_cached_enrichment(publications: list[Publication]) -> list[Publication]:
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return publications
    records = load_store().get("records", {})
    result: list[Publication] = []
    for publication in publications:
        updated_offers: list[Offer] = []
        changed = False
        for offer in publication.structured_offers:
            row = records.get(offer_fingerprint(offer))
            facts = row.get("facts") if isinstance(row, dict) and row.get("status") == "completed" else None
            if not isinstance(facts, dict):
                updated_offers.append(offer)
                continue
            updated = _copy_with_facts(offer, facts, config)
            updated_offers.append(updated)
            changed = changed or updated is not offer
        if changed:
            result.append(publication.model_copy(update={"structured_offers": updated_offers}, deep=True))
        else:
            result.append(publication)
    return result


def ai_member_payload(offer: Offer) -> dict[str, Any] | None:
    config = load_config()
    if not config.get("enabled") or not config.get("apply_results"):
        return None
    facts = getattr(offer, "ai_enrichment", None)
    if not isinstance(facts, dict) or not facts.get("same_offer"):
        return None
    if float(facts.get("pricing_confidence") or 0) < float(config.get("min_apply_confidence", 0.96)):
        return None
    return facts


def status_payload() -> dict[str, Any]:
    config = load_config()
    store = load_store()
    records = store.get("records", {})
    counts: dict[str, int] = {}
    for row in records.values():
        if isinstance(row, dict):
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
    return {
        "enabled": bool(config.get("enabled")),
        "apply_results": bool(config.get("apply_results")),
        "model": config.get("model"),
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "usage": usage_status(config, store),
        "records": counts,
        "scan_interval_seconds": int(config.get("scan_interval_seconds", 3600)),
    }

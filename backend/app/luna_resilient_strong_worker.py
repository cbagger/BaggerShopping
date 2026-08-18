from __future__ import annotations

"""Strength-preserving entrypoint for the resilient Luna worker.

The resilient publication contract from PR #94 is correct: source flyers must
remain usable even when AI enrichment is unavailable or one offer is ambiguous.
This entrypoint preserves the previous Luna enrichment breadth while adding a
small, persistent retry budget before an unresolved candidate is quarantined.
"""

import asyncio
import json
import os
from pathlib import Path

from . import luna_resilient_worker as base


# Match the established Luna scan breadth instead of weakening enrichment merely
# because publication release is now decoupled from AI processing. Operators can
# still override these values explicitly through the environment.
os.environ.setdefault("LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE", "20")
os.environ.setdefault("LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE", "10")
os.environ.setdefault("LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE", "10")
os.environ.setdefault("LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE", "20")
os.environ.setdefault("LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE", "5")

DEFAULT_FAILURE_ATTEMPTS = 2


def _retry_path() -> Path:
    return Path(os.getenv("LUNA_RETRY_STATE_PATH", "/data/luna-retry-work.json"))


def _load_retry_state() -> dict[str, dict]:
    path = _retry_path()
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = value.get("items") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {
        str(key): dict(row)
        for key, row in rows.items()
        if isinstance(row, dict)
    }


def _save_retry_state(rows: dict[str, dict]) -> None:
    path = _retry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(rows) > 4000:
        rows = dict(list(rows.items())[-4000:])
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "contract": base.RESILIENCE_CONTRACT_VERSION,
                "items": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "utf-8",
    )
    temporary.replace(path)


def _max_failure_attempts() -> int:
    try:
        return max(
            1,
            int(os.getenv("LUNA_RESILIENT_FAILURE_ATTEMPTS", str(DEFAULT_FAILURE_ATTEMPTS))),
        )
    except (TypeError, ValueError):
        return DEFAULT_FAILURE_ATTEMPTS


_original_quarantine = base._quarantine


def _bounded_quarantine(kind: str, publication, candidate, reason: str) -> None:
    """Retry unresolved AI work a bounded number of times, never forever.

    Deterministic provider equivalence is not a Luna failure and is safely
    suppressed immediately. Every other unresolved candidate gets a persistent
    per-source-generation attempt budget. Once exhausted, the original
    fail-closed quarantine takes over for that one offer only.
    """
    if str(reason) == "deterministic-provider-unit-equivalence":
        _original_quarantine(kind, publication, candidate, reason)
        return

    key = base._quarantine_key(kind, publication, candidate)
    rows = _load_retry_state()
    row = dict(rows.get(key) or {})
    attempts = int(row.get("attempts") or 0) + 1
    rows[key] = {
        "contract": base.RESILIENCE_CONTRACT_VERSION,
        "kind": str(kind),
        "publication_id": str(publication.id),
        "publication_fingerprint": base._source_fingerprint(publication),
        "candidate_fingerprint": base._candidate_fingerprint(candidate),
        "attempts": attempts,
        "last_reason": str(reason)[:500],
    }
    _save_retry_state(rows)

    if attempts >= _max_failure_attempts():
        _original_quarantine(kind, publication, candidate, reason)


def install_strength_policy() -> None:
    """Install retry policy only in the real worker process.

    Keeping installation explicit avoids import-time monkeypatch side effects in
    tests or diagnostic tools while preserving every validated Luna primitive.
    """
    base._quarantine = _bounded_quarantine


async def run_once() -> dict:
    install_strength_policy()
    return await base.run_once()


async def main() -> None:
    install_strength_policy()
    await base.main()


if __name__ == "__main__":
    asyncio.run(main())

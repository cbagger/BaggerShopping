from __future__ import annotations

import asyncio
import logging
import os

import httpx

from .flyer_adapters import fetch_all_publications
from .luna_enrichment import analyze_candidate, collect_candidates, load_config, status_payload

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("kurv-luna")


async def run_once() -> dict:
    config = load_config()
    if not config.get("enabled"):
        return {"status": "disabled", **status_payload()}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"status": "missing-api-key", **status_payload()}

    # The worker fetches exactly the same deterministic flyer data as Kurv.
    # Luna is never the source of truth for flyer discovery, geometry or basic
    # app availability; it only verifies candidates selected by the AI gate.
    publications = await fetch_all_publications()
    candidates = collect_candidates(publications)
    limit = max(1, int(config.get("max_requests_per_scan", 20)))
    selected = candidates[:limit]
    if not selected:
        return {"status": "idle", "candidates": 0, **status_payload()}

    processed = 0
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        for candidate in selected:
            result = await analyze_candidate(candidate, client=client)
            if result.get("status") in {"budget-exhausted", "disabled", "missing-api-key"}:
                break
            processed += 1
    return {
        "status": "processed",
        "candidates": len(candidates),
        "processed": processed,
        **status_payload(),
    }


async def main() -> None:
    while True:
        try:
            result = await run_once()
            log.info("Luna cycle: %s", result)
        except Exception:
            # The worker is intentionally isolated. A Luna/OpenAI failure must
            # never terminate or degrade the normal Kurv backend.
            log.exception("Luna enrichment cycle failed")
        config = load_config()
        await asyncio.sleep(max(300, int(config.get("scan_interval_seconds", 3600))))


if __name__ == "__main__":
    asyncio.run(main())

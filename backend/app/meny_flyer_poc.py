from __future__ import annotations

import argparse
import asyncio
import json

from .meny_flyer import search_live_meny_flyer


async def _run(query: str) -> int:
    result = await search_live_meny_flyer(query)
    payload = {
        "ok": result.ok,
        "retailer": result.retailer,
        "query": result.query,
        "publication": {
            "title": result.publication.title,
            "week": result.publication.week,
            "year": result.publication.year,
            "valid_from": result.publication.valid_from,
            "valid_until": result.publication.valid_until,
            "source_url": result.publication.source_url,
        },
        "match_count": len(result.matches),
        "matches": result.matches,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.matches else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only official MENY flyer proof of concept")
    parser.add_argument("query", help="Text to look for in the current MENY flyer, e.g. juice")
    args = parser.parse_args()
    return asyncio.run(_run(args.query))


if __name__ == "__main__":
    raise SystemExit(main())

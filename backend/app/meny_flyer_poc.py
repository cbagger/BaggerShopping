from __future__ import annotations

import argparse
import asyncio
import json

from .meny_flyer import fetch_meny_flyer, search_live_meny_flyer


async def _run(query: str | None, status_only: bool) -> int:
    if status_only:
        publication = await fetch_meny_flyer()
        payload = {
            "ok": publication.ok,
            "retailer": publication.retailer,
            "publication": {
                "title": publication.title,
                "week": publication.week,
                "year": publication.year,
                "valid_from": publication.valid_from,
                "valid_until": publication.valid_until,
                "source_url": publication.source_url,
                "content_source": getattr(publication, "content_source", None),
                "page_count": getattr(publication, "page_count", None),
                "text_length": len(publication.text),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not query:
        raise ValueError("query is required unless --status is used")

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
            "content_source": getattr(result.publication, "content_source", None),
            "page_count": getattr(result.publication, "page_count", None),
            "text_length": len(result.publication.text),
        },
        "match_count": len(result.matches),
        "matches": result.matches,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.matches else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only official MENY flyer proof of concept")
    parser.add_argument("query", nargs="?", help="Text to look for in the current MENY flyer, e.g. juice")
    parser.add_argument("--status", action="store_true", help="Print current publication metadata and parser source")
    args = parser.parse_args()
    return asyncio.run(_run(args.query, args.status))


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json

from .offers import fetch_goma_offers


async def _run(query: str, retailer: str) -> int:
    result = await fetch_goma_offers(query, retailer)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0 if result.offers else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Goma offers proof of concept")
    parser.add_argument("query", help="Goma category/search term, e.g. margarine")
    parser.add_argument("--retailer", default="MENY", help="Retailer to keep (default: MENY)")
    args = parser.parse_args()
    return asyncio.run(_run(args.query, args.retailer))


if __name__ == "__main__":
    raise SystemExit(main())

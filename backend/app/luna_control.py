from __future__ import annotations

import argparse
import asyncio
import json

from .luna_enrichment import save_config, status_payload
from .luna_semantic_audit import semantic_status_payload


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Kurv Luna backend control")
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("enable")
    sub.add_parser("disable")
    budget = sub.add_parser("budget")
    budget.add_argument("dkk", type=float)
    sub.add_parser("run-once")
    return value


def _status() -> dict:
    return {**status_payload(), **semantic_status_payload()}


def main() -> None:
    args = parser().parse_args()
    if args.command == "enable":
        save_config({"enabled": True})
    elif args.command == "disable":
        # Master switch: OFF means zero OpenAI calls and zero application of
        # cached Luna page/crop facts. Deterministic Kurv remains fully usable.
        save_config({"enabled": False})
    elif args.command == "budget":
        if args.dkk < 0:
            raise SystemExit("Budget must be >= 0")
        save_config({"monthly_budget_dkk": args.dkk})
    elif args.command == "run-once":
        from .luna_worker import run_once
        print(json.dumps(asyncio.run(run_once()), ensure_ascii=False, indent=2))
        return
    print(json.dumps(_status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

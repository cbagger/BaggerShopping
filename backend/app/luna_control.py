from __future__ import annotations

import argparse
import asyncio
import json

from .luna_enrichment import load_config, save_config, status_payload
from .luna_semantic_audit import semantic_status_payload
from .luna_cost_policy import status_payload as cost_policy_status


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
    config = load_config()
    total = max(1, int(config.get("max_requests_per_scan", 20)))
    semantic_config = {
        "master_enabled": bool(config.get("enabled")),
        "max_requests_per_scan": total,
        "max_page_audits_per_scan": max(
            1, int(config.get("max_page_audits_per_scan", max(1, total // 2)))
        ),
        "max_crop_verifications_per_scan": max(
            1, int(config.get("max_crop_verifications_per_scan", 10))
        ),
        "page_audit_max_failures": max(1, int(config.get("page_audit_max_failures", 2))),
        "page_audit_max_output_tokens": int(config.get("page_audit_max_output_tokens", 4000)),
        "page_scout_max_output_tokens": int(config.get("page_scout_max_output_tokens", 1400)),
        "crop_max_output_tokens": int(config.get("crop_max_output_tokens", 1200)),
    }
    return {
        **status_payload(),
        **semantic_status_payload(),
        "semantic_config": semantic_config,
        "cost_policy": cost_policy_status(),
    }


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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from core.learning_paths import DEFAULT_DB_PATH

from .report_builder import build_postmortem_report
from .storage import write_report_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate intelligence postmortem for a trade")
    parser.add_argument("trade_id", type=int)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    report = build_postmortem_report(args.trade_id, db_path=args.db)
    if report is None:
        print(json.dumps({"ok": False, "error": "trade not found"}, ensure_ascii=False))
        return 1
    path = write_report_artifact(f"postmortem_trade_{args.trade_id}.json", report)
    print(json.dumps({"ok": True, "path": path, "severity": report.get("severity")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from core.learning_paths import DEFAULT_DB_PATH

from .collector import collect_runtime_dataset
from .report_builder import build_and_store_advisories, build_daily_report
from .storage import write_report_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate daily intelligence report")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    dataset = collect_runtime_dataset(db_path=args.db, hours=args.hours)
    report = build_daily_report(dataset)
    path = write_report_artifact("daily_report.json", report)
    build_and_store_advisories(dataset, db_path=args.db)
    print(json.dumps({"ok": True, "path": path, "summary": report.get("summary_text")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

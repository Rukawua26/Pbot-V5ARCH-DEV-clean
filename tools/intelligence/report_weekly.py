#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from core.learning_paths import DEFAULT_DB_PATH

from .collector import collect_runtime_dataset
from .report_builder import build_weekly_report
from .storage import write_report_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate weekly intelligence report")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--hours", type=int, default=24 * 7)
    args = parser.parse_args()
    dataset = collect_runtime_dataset(db_path=args.db, hours=args.hours)
    report = build_weekly_report(dataset)
    path = write_report_artifact("weekly_report.json", report)
    print(json.dumps({"ok": True, "path": path, "report_type": "weekly"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

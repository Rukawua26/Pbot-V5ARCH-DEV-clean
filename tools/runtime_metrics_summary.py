#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def summarize_runtime_metrics(path: str | Path = "logs/runtime_metrics.jsonl") -> dict[str, Any]:
    metrics_path = Path(path)
    if not metrics_path.exists():
        return {"rows": 0, "exchange_calls": {}, "errors": {}}

    rows = 0
    calls: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    metric_counts: Counter[str] = Counter()
    latencies: defaultdict[str, list[float]] = defaultdict(list)
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows += 1
        metric_counts[str(record.get("metric") or "UNKNOWN")] += 1
        if record.get("metric") != "exchange_call":
            continue
        payload = record.get("payload") or {}
        op = str(payload.get("op") or "UNKNOWN")
        calls[op] += 1
        if not payload.get("ok", False):
            errors[str(payload.get("error_type") or "UNKNOWN")] += 1
        try:
            latencies[op].append(float(payload.get("latency_ms") or 0.0))
        except (TypeError, ValueError):
            pass

    exchange_calls = {}
    for op, count in calls.most_common():
        samples = latencies.get(op) or []
        exchange_calls[op] = {
            "count": count,
            "avg_latency_ms": round(sum(samples) / len(samples), 3) if samples else 0.0,
            "max_latency_ms": round(max(samples), 3) if samples else 0.0,
        }
    return {
        "rows": rows,
        "metrics": dict(metric_counts.most_common()),
        "exchange_calls": exchange_calls,
        "errors": dict(errors.most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize local runtime metrics JSONL")
    parser.add_argument("--path", default="logs/runtime_metrics.jsonl")
    args = parser.parse_args()
    print(json.dumps(summarize_runtime_metrics(args.path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay read-only counterfactual decisions from persisted signal alerts."""

from __future__ import annotations

import argparse
import errno
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

SCENARIOS = ("observed", "bull-off", "bull-directional", "coherence-off", "combined")
BULL_REGIMES = {"BULL_TREND", "BULL_STRONG"}
MAX_ROWS = 100_000
MAX_FEATURES_JSON_CHARS = 1_000_000
MAX_TOTAL_FEATURES_JSON_CHARS = 25_000_000
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _normalize_timestamp_bound(value: str | None, option: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{option} must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is not None:
        raise ValueError(f"{option} must be timezone-naive")
    return parsed.isoformat()


def _load_rows(
    db_path: Path,
    *,
    latest: int | None,
    since: str | None,
    until: str | None,
    strict: bool,
) -> list[dict]:
    if latest is not None and not 1 <= latest <= MAX_ROWS:
        raise ValueError(f"latest must be between 1 and {MAX_ROWS}")
    since = _normalize_timestamp_bound(since, "since")
    until = _normalize_timestamp_bound(until, "until")
    if since is not None and until is not None and since >= until:
        raise ValueError("since must be earlier than until")

    connection = _open_read_only(db_path)
    parsed = []
    total_features_chars = 0
    try:
        where = []
        params: list[object] = []
        if since:
            where.append("ts >= ?")
            params.append(since)
        if until:
            where.append("ts < ?")
            params.append(until)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        order_sql = "ORDER BY ts DESC, id DESC" if latest is not None else "ORDER BY ts, id"
        limit_sql = " LIMIT ?"
        params.append(latest if latest is not None else MAX_ROWS + 1)
        rows = connection.execute(
            "SELECT id, ts, symbol, alert_type, execution_mode, status, "
            "CASE WHEN length(features_json) <= ? THEN features_json ELSE NULL END "
            "AS features_json, COALESCE(length(features_json), 0) AS features_json_chars "
            f"FROM signal_alerts {where_sql} {order_sql}{limit_sql}",
            [MAX_FEATURES_JSON_CHARS, *params],
        )
        for row_count, row in enumerate(rows, start=1):
            if latest is None and row_count > MAX_ROWS:
                raise ValueError(f"Replay window exceeds the {MAX_ROWS}-row safety limit")

            features_chars = int(row["features_json_chars"])
            if features_chars > MAX_FEATURES_JSON_CHARS:
                error = ValueError("features_json exceeds the size limit")
                if strict:
                    raise ValueError(
                        f"Invalid features_json for signal_alerts.id={row['id']}"
                    ) from error
                features = {}
                features_state = "invalid"
            else:
                total_features_chars += features_chars
                if total_features_chars > MAX_TOTAL_FEATURES_JSON_CHARS:
                    raise ValueError("Replay window exceeds the total features_json size limit")
                raw_features = row["features_json"]
                features_state = "missing"
                features = {}
                try:
                    if raw_features not in (None, ""):
                        features = json.loads(raw_features)
                        if not isinstance(features, dict):
                            raise ValueError("features_json is not an object")
                        features_state = "valid"
                except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as error:
                    if strict:
                        raise ValueError(
                            f"Invalid features_json for signal_alerts.id={row['id']}"
                        ) from error
                    features = {}
                    features_state = "invalid"

            row_data = dict(row)
            row_data.pop("features_json_chars")
            parsed.append({**row_data, "features": features, "features_state": features_state})
    finally:
        connection.close()

    if latest is not None:
        parsed.reverse()
    return parsed


def _reason(row: dict) -> str:
    features = row["features"]
    return str(features.get("filter_reason") or features.get("audit_verdict") or "").strip()


def _is_blocked(row: dict) -> bool:
    features = row["features"]
    reason = _reason(row).upper()
    return (
        features.get("filter_passed") is False
        or "VETO" in reason
        or reason.startswith("COHERENCIA:")
    )


def _observed(row: dict) -> str:
    features = row["features"]
    status = str(row["status"] or "").upper()
    execution_mode = str(row["execution_mode"] or "").upper()
    if status in {"EXECUTED", "VETOED", "REJECTED", "ERROR"}:
        return f"RECORDED_{status}"
    if _is_blocked(row):
        return "RECORDED_BLOCKED"
    if execution_mode == "BOOTSTRAP_NONE":
        return "RECORDED_NO_FIRE"
    if execution_mode != "NONE":
        if status == "PENDING":
            return "RECORDED_ENTRY_PIPELINE_PENDING"
        return f"RECORDED_{status or 'UNKNOWN'}"
    if features.get("filter_passed") is True:
        return "RECORDED_FILTER_PASS"
    if status == "DISCARDED":
        return "RECORDED_DISCARDED"
    return "UNKNOWN"


def _released_coherence(row: dict) -> str:
    features = row["features"]
    if features.get("bootstrap_ready_real") is True:
        return "ELIGIBLE_PRIMARY_CANDIDATE"
    if features.get("bootstrap_ready_shadow") is True:
        return "ELIGIBLE_SHADOW_CANDIDATE"
    if "bootstrap_ready_real" in features or "bootstrap_ready_shadow" in features:
        return "BOOTSTRAP_NO_FIRE"
    return "RELEASED_UNKNOWN_PLAN"


def classify(row: dict, scenario: str) -> str:
    if scenario == "observed":
        return _observed(row)

    features = row["features"]
    side = str(row["alert_type"] or features.get("side") or "").upper()
    regime = str(features.get("btc_regime") or "").upper()
    reason = _reason(row)
    bull_veto = reason.upper().startswith("BULL_TREND_ENTRY_VETO")
    coherence_veto = reason.upper().startswith("COHERENCIA:")

    if scenario == "bull-off" and bull_veto:
        return "RELEASED_UNKNOWN_DOWNSTREAM"

    if scenario in {"bull-directional", "combined"} and regime in BULL_REGIMES:
        if side == "SELL":
            if bull_veto:
                return "REMAINS_BLOCKED_BULL_COUNTER"
            if _is_blocked(row):
                return "UNCHANGED_BLOCKED"
            return "NEW_BLOCK_BULL_COUNTER"
        if side == "BUY" and bull_veto:
            paper_mode = features.get("paper_mode")
            if paper_mode is True:
                return "RELEASED_UNKNOWN_DOWNSTREAM"
            if paper_mode is False:
                return "REMAINS_BLOCKED_BULL_REAL"
            return "RUNTIME_MODE_CENSORED"

    if scenario in {"coherence-off", "combined"} and coherence_veto:
        return _released_coherence(row)

    return "UNCHANGED_BLOCKED" if _is_blocked(row) else "UNCHANGED"


def build_report(rows: list[dict], scenarios: list[str], db_path: Path) -> dict:
    coverage = Counter(
        {
            "valid_feature_objects": 0,
            "invalid_feature_objects": 0,
            "missing_feature_objects": 0,
            "nonempty_feature_objects": 0,
        }
    )
    for row in rows:
        features = row["features"]
        features_state = row.get("features_state", "valid")
        coverage[f"{features_state}_feature_objects"] += 1
        coverage["nonempty_feature_objects"] += int(bool(features))
        coverage["has_btc_regime"] += int("btc_regime" in features)
        coverage["has_sentiment"] += int("current_sentiment" in features or "sentiment" in features)

    return {
        "source": {
            "db": db_path.name,
            "read_only": True,
            "read_only_scope": "database_content",
            "rows": len(rows),
            "first_ts": rows[0]["ts"] if rows else None,
            "last_ts": rows[-1]["ts"] if rows else None,
        },
        "coverage": dict(coverage),
        "scenarios": {
            scenario: dict(Counter(classify(row, scenario) for row in rows))
            for scenario in scenarios
        },
        "limitations": [
            "historical_config_not_snapshotted",
            "runtime_mode_may_be_missing",
            "current_sentiment_may_be_missing",
            "released_rows_are_downstream_censored",
            "status_is_not_an_execution_outcome",
            "timestamps_are_timezone_naive",
            "sqlite_may_create_wal_sidecars",
        ],
    }


def _write_output(output_path: Path, db_path: Path, rendered: str) -> None:
    source_path = db_path.resolve(strict=True)
    protected_paths = [
        source_path,
        *(Path(f"{source_path}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES),
    ]
    resolved_output = output_path.resolve(strict=False)
    if resolved_output in protected_paths:
        raise ValueError("Output path must not refer to the source database or its sidecars")

    protected_inodes = {
        (path.stat().st_dev, path.stat().st_ino) for path in protected_paths if path.exists()
    }
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("Output path must not be a symbolic link") from error
        raise

    try:
        output_stat = os.fstat(descriptor)
        if (output_stat.st_dev, output_stat.st_ino) in protected_inodes:
            raise ValueError("Output path must not refer to the source database or its sidecars")
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            descriptor = -1
            output_file.write(rendered + "\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--latest", type=int, default=721)
    window.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.latest is not None and args.latest < 1:
        raise SystemExit("--latest must be >= 1")
    scenarios = args.scenario or list(SCENARIOS)
    rows = _load_rows(
        args.db,
        latest=args.latest if args.since is None else None,
        since=args.since,
        until=args.until,
        strict=args.strict,
    )
    report = build_report(rows, scenarios, args.db)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        _write_output(args.output, args.db, rendered)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

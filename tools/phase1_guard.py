#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlite3


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "sniper_brain.db"
CONFIG_PATH = ROOT / "core" / "config" / "manager.py"
LOCK_PATH = ROOT / ".sniperai.lock"
LOG_DIR = ROOT / "logs"
STATE_PATH = LOG_DIR / "phase1_guard_state.json"
REPORT_PATH = LOG_DIR / "phase1_guard_report.md"
EVENTS_PATH = LOG_DIR / "phase1_guard_events.jsonl"


@dataclass
class WindowStats:
    count: int
    wins: int
    losses: int
    wr: float | None


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _load_state(horizon_hours: int) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"⚠️ No se pudo cargar estado guardado ({exc}), creando nuevo.")

    now = _now_utc()
    state = {
        "activated_at": _iso(now),
        "expires_at": _iso(now + timedelta(hours=horizon_hours)),
        "phase": "PHASE_1",
        "rollback_applied": False,
    }
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    return state


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def _read_breakout_min() -> float | None:
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except Exception:
        return None
    match = re.search(r"^\s*BREAKOUT_MIN_IA_PROB\s*=\s*([0-9.]+)", text, re.MULTILINE)
    if not match:
        return None
    return float(match.group(1))


def _set_breakout_min(value: float) -> bool:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    updated = re.sub(
        r"(^\s*BREAKOUT_MIN_IA_PROB\s*=\s*)([0-9.]+)",
        rf"\g<1>{value:.1f}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == text:
        return False
    CONFIG_PATH.write_text(updated, encoding="utf-8")
    return True


def _query_window(
    conn: sqlite3.Connection, start: datetime, end: datetime
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, timestamp, pnl_percent, is_shadow
        FROM trades
        WHERE is_shadow = 0
          AND pnl_percent IS NOT NULL
          AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (_iso(start), _iso(end)),
    )
    return cur.fetchall()


def _stats(rows: list[sqlite3.Row]) -> WindowStats:
    count = len(rows)
    wins = sum(1 for r in rows if float(r["pnl_percent"] or 0.0) > 0.0)
    losses = sum(1 for r in rows if float(r["pnl_percent"] or 0.0) <= 0.0)
    wr = (wins / count) * 100.0 if count > 0 else None
    return WindowStats(count=count, wins=wins, losses=losses, wr=wr)


def _last_two_losses(
    conn: sqlite3.Connection, end: datetime
) -> tuple[bool, list[dict[str, Any]]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, timestamp, pnl_percent
        FROM trades
        WHERE is_shadow = 0
          AND pnl_percent IS NOT NULL
          AND timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 2
        """,
        (_iso(end - timedelta(hours=6)),),
    )
    rows = cur.fetchall()
    payload = [
        {
            "id": int(r["id"]),
            "timestamp": str(r["timestamp"]),
            "pnl_percent": float(r["pnl_percent"] or 0.0),
        }
        for r in rows
    ]
    if len(payload) < 2:
        return False, payload
    return payload[0]["pnl_percent"] <= 0.0 and payload[1][
        "pnl_percent"
    ] <= 0.0, payload


def _restart_from_lock() -> tuple[bool, str]:
    if not LOCK_PATH.exists():
        return False, "lock_missing"
    try:
        pid_txt = LOCK_PATH.read_text(encoding="utf-8").strip()
        pid = int(pid_txt)
        os.kill(pid, signal.SIGTERM)
        return True, f"sigterm_sent_pid_{pid}"
    except Exception as error:
        return False, f"restart_failed:{error}"


def _append_event(payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload["ts"] = _iso(_now_utc())
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _fmt_wr(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 guard + rollback policy")
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--rollback-on-trigger", action="store_true")
    parser.add_argument("--min-sample", type=int, default=5)
    args = parser.parse_args()

    state = _load_state(args.horizon_hours)
    now = _now_utc()
    expires_at = datetime.fromisoformat(state["expires_at"])
    monitoring_active = now <= expires_at

    breakout_min = _read_breakout_min()

    current_start = now - timedelta(hours=6)
    previous_start = now - timedelta(hours=12)
    previous_end = now - timedelta(hours=6)

    current_rows: list[sqlite3.Row] = []
    previous_rows: list[sqlite3.Row] = []
    two_losses = False
    last_two: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        conn = sqlite3.connect(DB_PATH)
        current_rows = _query_window(conn, current_start, now)
        previous_rows = _query_window(conn, previous_start, previous_end)
        two_losses, last_two = _last_two_losses(conn, now)
        conn.close()
    except Exception as error:
        errors.append(str(error))

    current_stats = _stats(current_rows)
    previous_stats = _stats(previous_rows)

    wr_drop = None
    if current_stats.wr is not None and previous_stats.wr is not None:
        wr_drop = previous_stats.wr - current_stats.wr

    wr_drop_trigger = bool(
        current_stats.count >= args.min_sample
        and previous_stats.count >= args.min_sample
        and wr_drop is not None
        and wr_drop >= 10.0
    )
    trigger = monitoring_active and (two_losses or wr_drop_trigger)

    rollback_changed = False
    restart_sent = False
    restart_msg = "not_requested"

    if (
        args.rollback_on_trigger
        and trigger
        and not state.get("rollback_applied", False)
    ):
        if breakout_min is not None and breakout_min < 60.0:
            rollback_changed = _set_breakout_min(60.0)
        if rollback_changed:
            restart_sent, restart_msg = _restart_from_lock()
            state["rollback_applied"] = True
            state["rollback_at"] = _iso(now)
            _save_state(state)

    report = []
    report.append("# Phase 1 Guard Report")
    report.append("")
    report.append(f"- Generated (UTC): {_iso(now)}")
    report.append(
        f"- Monitoring horizon: {state['activated_at']} -> {state['expires_at']}"
    )
    report.append(f"- Monitoring active: {'YES' if monitoring_active else 'NO'}")
    report.append(
        f"- BREAKOUT_MIN_IA_PROB actual: {breakout_min if breakout_min is not None else 'N/A'}"
    )
    report.append("")
    report.append("## Last 6h vs Previous 6h (REAL trades)")
    report.append("")
    report.append(
        f"- Current 6h: trades={current_stats.count}, wins={current_stats.wins}, losses={current_stats.losses}, WR={_fmt_wr(current_stats.wr)}"
    )
    report.append(
        f"- Previous 6h: trades={previous_stats.count}, wins={previous_stats.wins}, losses={previous_stats.losses}, WR={_fmt_wr(previous_stats.wr)}"
    )
    report.append(
        f"- WR drop: {f'{wr_drop:.1f} pts' if wr_drop is not None else 'N/A'}"
    )
    report.append("")
    report.append("## Rollback Policy")
    report.append("")
    report.append(
        f"- Trigger A (2 losses consecutivas): {'YES' if two_losses else 'NO'}"
    )
    report.append(
        f"- Trigger B (WR drop >= 10 pts in 6h): {'YES' if wr_drop_trigger else 'NO'}"
    )
    report.append(f"- Trigger final: {'YES' if trigger else 'NO'}")
    report.append(f"- Rollback changed config: {'YES' if rollback_changed else 'NO'}")
    report.append(
        f"- Restart signal sent: {'YES' if restart_sent else 'NO'} ({restart_msg})"
    )

    if last_two:
        report.append("")
        report.append("## Last 2 REAL trades")
        report.append("")
        for row in last_two:
            report.append(
                f"- id={row['id']} ts={row['timestamp']} pnl={row['pnl_percent']:.4f}%"
            )

    if errors:
        report.append("")
        report.append("## Errors")
        report.append("")
        for error in errors:
            report.append(f"- {error}")

    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")

    _append_event(
        {
            "phase": "PHASE_1",
            "monitoring_active": monitoring_active,
            "breakout_min": breakout_min,
            "current_6h_trades": current_stats.count,
            "previous_6h_trades": previous_stats.count,
            "current_6h_wr": current_stats.wr,
            "previous_6h_wr": previous_stats.wr,
            "wr_drop": wr_drop,
            "trigger_two_losses": two_losses,
            "trigger_wr_drop": wr_drop_trigger,
            "trigger_final": trigger,
            "rollback_changed": rollback_changed,
            "restart_sent": restart_sent,
            "restart_msg": restart_msg,
            "errors": errors,
        }
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

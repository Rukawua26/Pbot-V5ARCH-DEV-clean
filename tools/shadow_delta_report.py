#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


LOG_TS_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class WindowMetrics:
    label: str
    start: datetime
    end: datetime
    duration_hours: float
    signals_total: int
    verdict_real: int
    verdict_shadow: int
    verdict_veto: int
    veto_low_prob: int
    veto_kava: int
    veto_shock: int
    veto_mtf: int
    veto_other: int
    order_intents: int
    order_filled: int
    shadow_entries: int
    shadow_closed: int
    win_rate_pct: float
    expectancy_pct: float
    profit_factor: float
    net_pnl_pct: float
    max_drawdown_pct: float
    avg_mae_pct: float | None
    avg_mfe_pct: float | None
    degraded_rate_pct: float


def _parse_iso_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_log_ts(line: str) -> datetime | None:
    if len(line) < 19:
        return None
    try:
        parsed = datetime.strptime(line[:19], LOG_TS_FMT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _compute_drawdown_from_returns(returns_pct: Iterable[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret_pct in returns_pct:
        equity *= 1.0 + (float(ret_pct) / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - (equity / peak))
    return max_dd * 100.0


def _classify_veto_reason(verdict: str) -> str:
    text = (verdict or "").upper()
    if "BAJA PROB IA" in text:
        return "low_prob"
    if "VETO_KAVA" in text:
        return "kava"
    if "SHOCK DEMASIADO CERCA" in text:
        return "shock"
    if "MTF_VETO" in text:
        return "mtf"
    return "other"


def load_marker_ts(path: Path) -> datetime:
    data = json.loads(path.read_text(encoding="utf-8"))
    ts = _parse_iso_ts(data.get("ts"))
    if ts is None:
        raise SystemExit(f"marker inválido: {path}")
    return ts


def summarize_log(log_path: Path, start: datetime, end: datetime) -> dict[str, int]:
    out = {
        "signals_total": 0,
        "verdict_real": 0,
        "verdict_shadow": 0,
        "verdict_veto": 0,
        "veto_low_prob": 0,
        "veto_kava": 0,
        "veto_shock": 0,
        "veto_mtf": 0,
        "veto_other": 0,
    }
    if not log_path.exists():
        return out

    with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            ts = _parse_log_ts(line)
            if ts is None or ts < start or ts >= end or "verdict=" not in line:
                continue
            out["signals_total"] += 1
            verdict = line.split("verdict=", 1)[1].strip()
            if verdict.startswith("🚀"):
                out["verdict_real"] += 1
            elif verdict.startswith("👻") or verdict.startswith("🧪"):
                out["verdict_shadow"] += 1
            else:
                out["verdict_veto"] += 1
                bucket = _classify_veto_reason(verdict)
                out[f"veto_{bucket}"] += 1
    return out


def summarize_execution_events(events_path: Path, start: datetime, end: datetime) -> dict[str, int]:
    counts = {
        "order_intents": 0,
        "order_filled": 0,
    }
    if not events_path.exists():
        return counts

    with events_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso_ts(row.get("ts"))
            if ts is None or ts < start or ts >= end:
                continue
            event = str(row.get("event") or "")
            if event == "ORDER_INTENT_CREATED":
                counts["order_intents"] += 1
            elif event == "ORDER_FILLED":
                counts["order_filled"] += 1
    return counts


def summarize_trades(db_path: Path, start: datetime, end: datetime) -> dict[str, Any]:
    out = {
        "shadow_entries": 0,
        "shadow_closed": 0,
        "win_rate_pct": 0.0,
        "expectancy_pct": 0.0,
        "profit_factor": 0.0,
        "net_pnl_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "avg_mae_pct": None,
        "avg_mfe_pct": None,
        "degraded_rate_pct": 0.0,
    }
    if not db_path.exists():
        return out

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT symbol, timestamp, open_time, pnl_percent, mae_percent, mfe_percent, reason, is_shadow
            FROM trades
            WHERE is_shadow = 1
              AND symbol != 'SYSTEM'
            """
        ).fetchall()
    finally:
        conn.close()

    opened: list[sqlite3.Row] = []
    closed: list[sqlite3.Row] = []
    for row in rows:
        open_ts = _parse_iso_ts(row["open_time"])
        close_ts = _parse_iso_ts(row["timestamp"])
        if open_ts is not None and start <= open_ts < end:
            opened.append(row)
        if close_ts is not None and start <= close_ts < end and row["pnl_percent"] is not None:
            closed.append(row)

    returns = [float(row["pnl_percent"] or 0.0) for row in closed]
    wins = [ret for ret in returns if ret > 0.0]
    losses = [ret for ret in returns if ret <= 0.0]
    mae_vals = [float(row["mae_percent"]) for row in closed if row["mae_percent"] is not None]
    mfe_vals = [float(row["mfe_percent"]) for row in closed if row["mfe_percent"] is not None]
    degraded = 0
    for row in closed:
        reason = str(row["reason"] or "").upper()
        if any(
            token in reason
            for token in (
                "DEGRADED_",
                "CONF_DEGRADED_",
                "CONFIDENCE_FLOOR_VIOLATED",
                "SUDDEN_CONFIDENCE_CRASH",
                "SHORT_THESIS_INVALIDATED",
            )
        ):
            degraded += 1

    out["shadow_entries"] = len(opened)
    out["shadow_closed"] = len(closed)
    out["win_rate_pct"] = _safe_div(len(wins) * 100.0, len(closed))
    out["expectancy_pct"] = _safe_div(sum(returns), len(closed))
    out["profit_factor"] = _safe_div(sum(wins), abs(sum(losses)))
    out["net_pnl_pct"] = sum(returns)
    out["max_drawdown_pct"] = _compute_drawdown_from_returns(returns)
    out["avg_mae_pct"] = _safe_div(sum(mae_vals), len(mae_vals)) if mae_vals else None
    out["avg_mfe_pct"] = _safe_div(sum(mfe_vals), len(mfe_vals)) if mfe_vals else None
    out["degraded_rate_pct"] = _safe_div(degraded * 100.0, len(closed))
    return out


def build_window_metrics(
    *,
    label: str,
    start: datetime,
    end: datetime,
    log_path: Path,
    events_path: Path,
    db_path: Path,
) -> WindowMetrics:
    log_summary = summarize_log(log_path, start, end)
    event_summary = summarize_execution_events(events_path, start, end)
    trade_summary = summarize_trades(db_path, start, end)
    return WindowMetrics(
        label=label,
        start=start,
        end=end,
        duration_hours=(end - start).total_seconds() / 3600.0,
        signals_total=log_summary["signals_total"],
        verdict_real=log_summary["verdict_real"],
        verdict_shadow=log_summary["verdict_shadow"],
        verdict_veto=log_summary["verdict_veto"],
        veto_low_prob=log_summary["veto_low_prob"],
        veto_kava=log_summary["veto_kava"],
        veto_shock=log_summary["veto_shock"],
        veto_mtf=log_summary["veto_mtf"],
        veto_other=log_summary["veto_other"],
        order_intents=event_summary["order_intents"],
        order_filled=event_summary["order_filled"],
        shadow_entries=trade_summary["shadow_entries"],
        shadow_closed=trade_summary["shadow_closed"],
        win_rate_pct=trade_summary["win_rate_pct"],
        expectancy_pct=trade_summary["expectancy_pct"],
        profit_factor=trade_summary["profit_factor"],
        net_pnl_pct=trade_summary["net_pnl_pct"],
        max_drawdown_pct=trade_summary["max_drawdown_pct"],
        avg_mae_pct=trade_summary["avg_mae_pct"],
        avg_mfe_pct=trade_summary["avg_mfe_pct"],
        degraded_rate_pct=trade_summary["degraded_rate_pct"],
    )


def _fmt_float(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def print_window(metrics: WindowMetrics) -> None:
    print(f"[{metrics.label}] {metrics.start.isoformat()} -> {metrics.end.isoformat()}")
    print(
        f"signals={metrics.signals_total} real={metrics.verdict_real} shadow={metrics.verdict_shadow} veto={metrics.verdict_veto}"
    )
    print(
        "veto_breakdown:"
        f" low_prob={metrics.veto_low_prob}"
        f" kava={metrics.veto_kava}"
        f" shock={metrics.veto_shock}"
        f" mtf={metrics.veto_mtf}"
        f" other={metrics.veto_other}"
    )
    print(
        f"execution: intents={metrics.order_intents} filled={metrics.order_filled}"
        f" entries={metrics.shadow_entries} closed={metrics.shadow_closed}"
    )
    print(
        "quality:"
        f" wr={metrics.win_rate_pct:.2f}%"
        f" exp={metrics.expectancy_pct:.4f}%"
        f" pf={metrics.profit_factor:.4f}"
        f" net={metrics.net_pnl_pct:+.4f}%"
        f" mdd={metrics.max_drawdown_pct:.4f}%"
        f" mae={_fmt_float(metrics.avg_mae_pct, 4)}%"
        f" mfe={_fmt_float(metrics.avg_mfe_pct, 4)}%"
        f" degraded={metrics.degraded_rate_pct:.2f}%"
    )


def print_delta(current: WindowMetrics, previous: WindowMetrics) -> None:
    print("[delta current - previous]")
    print(
        f"signals={current.signals_total - previous.signals_total:+d}"
        f" intents={current.order_intents - previous.order_intents:+d}"
        f" filled={current.order_filled - previous.order_filled:+d}"
        f" entries={current.shadow_entries - previous.shadow_entries:+d}"
        f" closed={current.shadow_closed - previous.shadow_closed:+d}"
    )
    print(
        f"wr={current.win_rate_pct - previous.win_rate_pct:+.2f}pp"
        f" exp={current.expectancy_pct - previous.expectancy_pct:+.4f}pp"
        f" pf={current.profit_factor - previous.profit_factor:+.4f}"
        f" net={current.net_pnl_pct - previous.net_pnl_pct:+.4f}pp"
        f" mdd={current.max_drawdown_pct - previous.max_drawdown_pct:+.4f}pp"
    )
    print(
        f"veto_low_prob={current.veto_low_prob - previous.veto_low_prob:+d}"
        f" veto_kava={current.veto_kava - previous.veto_kava:+d}"
        f" veto_shock={current.veto_shock - previous.veto_shock:+d}"
        f" veto_mtf={current.veto_mtf - previous.veto_mtf:+d}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compara la ventana actual desde un marker contra la ventana previa de igual duración"
    )
    parser.add_argument("--db", default="sniper_brain.db")
    parser.add_argument("--log", default="sniper.log")
    parser.add_argument("--events", default="logs/execution_events.jsonl")
    parser.add_argument("--marker", required=True)
    parser.add_argument("--end-ts", default="")
    args = parser.parse_args()

    marker_ts = load_marker_ts(Path(args.marker))
    end_ts = _parse_iso_ts(args.end_ts) if args.end_ts else datetime.now(timezone.utc)
    if end_ts is None:
        raise SystemExit("--end-ts inválido")
    if end_ts <= marker_ts:
        raise SystemExit("la ventana actual debe terminar después del marker")

    duration = end_ts - marker_ts
    prev_end = marker_ts
    prev_start = prev_end - duration

    current = build_window_metrics(
        label="current",
        start=marker_ts,
        end=end_ts,
        log_path=Path(args.log),
        events_path=Path(args.events),
        db_path=Path(args.db),
    )
    previous = build_window_metrics(
        label="previous",
        start=prev_start,
        end=prev_end,
        log_path=Path(args.log),
        events_path=Path(args.events),
        db_path=Path(args.db),
    )

    print_window(current)
    print()
    print_window(previous)
    print()
    print_delta(current, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

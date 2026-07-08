#!/usr/bin/env python3
"""Summarize SHADOW validation campaign metrics from runtime_metrics.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METRICS_PATH = PROJECT_ROOT / "logs" / "runtime_metrics.jsonl"


def _iter_shadow_events(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("metric") != "shadow_validation":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, dict):
                yield payload


def _pct(value: float, total: float) -> float:
    return round((value / total) * 100.0, 2) if total else 0.0


def build_summary(path: Path = DEFAULT_METRICS_PATH) -> dict[str, Any]:
    events = list(_iter_shadow_events(path) or [])
    filters = [e for e in events if e.get("event") == "filter_decision"]
    trades = [e for e in events if e.get("event") == "shadow_trade_closed"]
    fvg_cycles = [e for e in events if e.get("event") == "fvg_cycle"]
    config_snapshots = [e for e in events if e.get("event") == "config_snapshot"]

    vetoes = [e for e in filters if not bool(e.get("filter_passed", False))]
    macro_vetoes = [
        e
        for e in vetoes
        if "FEAR_" in str(e.get("filter_reason", ""))
        or "MARKET_BREADTH" in str(e.get("filter_reason", ""))
    ]
    macro_boosts = [e for e in filters if str(e.get("macro_boost_reason", ""))]
    overrides = [e for e in filters if bool(e.get("agent_signal_override", False))]

    winners = [e for e in trades if float(e.get("pnl_percent") or 0.0) > 0.0]
    losers = [e for e in trades if float(e.get("pnl_percent") or 0.0) <= 0.0]
    total_pnl = round(sum(float(e.get("pnl_usd") or 0.0) for e in trades), 4)
    total_pnl_pct = round(sum(float(e.get("pnl_percent") or 0.0) for e in trades), 4)
    avg_win = round(
        (sum(float(e.get("pnl_percent") or 0.0) for e in winners) / len(winners))
        if winners
        else 0.0,
        4,
    )
    avg_loss = round(
        (sum(float(e.get("pnl_percent") or 0.0) for e in losers) / len(losers)) if losers else 0.0,
        4,
    )
    max_mae = round(min((float(e.get("mae_percent") or 0.0) for e in trades), default=0.0), 4)
    max_mfe = round(max((float(e.get("mfe_percent") or 0.0) for e in trades), default=0.0), 4)

    fvg_new = sum(int(e.get("new_gaps") or 0) for e in fvg_cycles)
    last_fvg = fvg_cycles[-1] if fvg_cycles else {}

    return {
        "events": len(events),
        "latest_config": config_snapshots[-1] if config_snapshots else {},
        "filters": {
            "total": len(filters),
            "vetoes": len(vetoes),
            "veto_rate_pct": _pct(len(vetoes), len(filters)),
            "macro_vetoes": len(macro_vetoes),
            "macro_boosts": len(macro_boosts),
            "agent_overrides": len(overrides),
            "agent_override_rate_pct": _pct(len(overrides), len(filters)),
        },
        "shadow_trades": {
            "closed": len(trades),
            "wins": len(winners),
            "losses": len(losers),
            "winrate_pct": _pct(len(winners), len(trades)),
            "total_pnl_usd": total_pnl,
            "total_pnl_pct_sum": total_pnl_pct,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "max_mae_pct": max_mae,
            "max_mfe_pct": max_mfe,
        },
        "fvg": {
            "cycles": len(fvg_cycles),
            "new_gaps": fvg_new,
            "last_active_total": int(last_fvg.get("active_total") or 0),
            "last_status_counts": last_fvg.get("status_counts") or {},
        },
    }


def render_markdown(summary: dict[str, Any]) -> str:
    filters = summary["filters"]
    trades = summary["shadow_trades"]
    fvg = summary["fvg"]
    lines = ["# SHADOW Validation Report", ""]
    lines.append(f"- Events: {summary['events']}")
    lines.append(f"- Filters analyzed: {filters['total']}")
    lines.append(f"- Veto rate: {filters['veto_rate_pct']}%")
    lines.append(f"- Macro vetoes: {filters['macro_vetoes']}")
    lines.append(f"- Macro boosts: {filters['macro_boosts']}")
    lines.append(f"- Agent override rate: {filters['agent_override_rate_pct']}%")
    lines.append(f"- Closed SHADOW trades: {trades['closed']}")
    lines.append(f"- SHADOW winrate: {trades['winrate_pct']}%")
    lines.append(f"- SHADOW PnL USD: {trades['total_pnl_usd']}")
    lines.append(f"- Avg win/loss pct: {trades['avg_win_pct']} / {trades['avg_loss_pct']}")
    lines.append(f"- MAE/MFE pct: {trades['max_mae_pct']} / {trades['max_mfe_pct']}")
    lines.append(f"- FVG cycles/new gaps: {fvg['cycles']} / {fvg['new_gaps']}")
    lines.append(f"- FVG active statuses: {fvg['last_status_counts']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_METRICS_PATH)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    summary = build_summary(args.path)
    if args.as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

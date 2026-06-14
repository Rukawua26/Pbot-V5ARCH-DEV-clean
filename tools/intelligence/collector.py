from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from core.learning_paths import DEFAULT_DB_PATH

from .loaders import (
    DEFAULT_EVENTS_PATH,
    DEFAULT_STATE_PATH,
    load_execution_events,
    load_state_snapshot,
    load_trade_context_snapshots,
    load_trades,
    utc_now,
)
from .research_jobs import compare_shadow_vs_real, summarize_context_clusters, summarize_veto_impact
from .storage import ensure_intelligence_tables, upsert_trade_annotation


def _trade_labels(trade) -> tuple[str, str, str]:
    regime = str(trade.market_regime or "UNKNOWN")
    confidence = float(trade.entry_confidence or 0.0)
    pnl_pct = float(trade.pnl_percent or 0.0)
    context_label = f"{regime}_{'HIGH' if confidence >= 70 else 'MEDIUM' if confidence >= 55 else 'LOW'}"
    if pnl_pct > 1.5:
        risk_label = "WINNER"
    elif pnl_pct < -1.5:
        risk_label = "LOSER"
    else:
        risk_label = "NEUTRAL"
    mode = "SHADOW" if trade.is_shadow else "REAL"
    return context_label, risk_label, mode


def collect_runtime_dataset(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    events_path: str | Path = DEFAULT_EVENTS_PATH,
    state_path: str | Path = DEFAULT_STATE_PATH,
    hours: int = 24,
    trade_limit: int = 500,
) -> dict[str, Any]:
    ensure_intelligence_tables(db_path)
    since = utc_now() - timedelta(hours=hours)
    state = load_state_snapshot(state_path)
    events = load_execution_events(events_path, limit=1000, since=since)
    trades = load_trades(db_path, limit=trade_limit, since=since)
    snapshots = load_trade_context_snapshots(db_path, limit=trade_limit)

    for trade in trades:
        context_label, risk_label, mode = _trade_labels(trade)
        narrative = (
            f"{trade.symbol} {trade.side} {mode} cerrada con pnl_pct={float(trade.pnl_percent or 0.0):+.2f}% "
            f"en contexto {context_label}."
        )
        upsert_trade_annotation(
            trade.id,
            trade.symbol,
            mode,
            context_label,
            risk_label,
            narrative,
            {
                "pnl_percent": trade.pnl_percent,
                "reason": trade.reason,
                "entry_confidence": trade.entry_confidence,
                "exit_confidence": trade.exit_confidence,
                "market_regime": trade.market_regime,
            },
            db_path=db_path,
        )

    mode_counts = Counter("SHADOW" if trade.is_shadow else "REAL" for trade in trades)
    return {
        "generated_at": utc_now().isoformat(),
        "window_hours": hours,
        "state": state.to_dict() if state else None,
        "events": [event.to_dict() for event in events],
        "trades": [trade.to_dict() for trade in trades],
        "trade_context_snapshots": snapshots,
        "summary": {
            "event_count": len(events),
            "trade_count": len(trades),
            "shadow_trade_count": mode_counts.get("SHADOW", 0),
            "real_trade_count": mode_counts.get("REAL", 0),
        },
        "research": {
            "veto_impact": summarize_veto_impact(events),
            "shadow_vs_real": compare_shadow_vs_real(trades),
            "context_clusters": summarize_context_clusters(trades),
        },
    }

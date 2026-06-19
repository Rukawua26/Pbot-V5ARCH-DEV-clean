from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .contracts import ExecutionEventRecord, TradeRecord


def summarize_veto_impact(events: list[ExecutionEventRecord]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    by_symbol: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        payload = event.payload or {}
        reason = str(
            payload.get("filter_reason") or payload.get("reason") or event.event or "UNKNOWN"
        )
        symbol = str(payload.get("symbol") or "UNKNOWN")
        reasons[reason] += 1
        by_symbol[symbol][reason] += 1
    top_symbols: list[dict[str, Any]] = []
    for symbol, symbol_reasons in by_symbol.items():
        top_reason, top_count = symbol_reasons.most_common(1)[0]
        top_symbols.append(
            {
                "symbol": symbol,
                "top_reason": top_reason,
                "count": top_count,
                "total": sum(symbol_reasons.values()),
            }
        )
    top_symbols.sort(key=lambda item: int(item["total"]), reverse=True)
    return {
        "total_events": sum(reasons.values()),
        "reason_counts": [{"reason": key, "count": value} for key, value in reasons.most_common()],
        "top_symbols": top_symbols[:10],
    }


def compare_shadow_vs_real(trades: list[TradeRecord]) -> dict[str, Any]:
    groups: dict[str, list[TradeRecord]] = {"shadow": [], "real": []}
    for trade in trades:
        groups["shadow" if trade.is_shadow else "real"].append(trade)

    def _stats(rows: list[TradeRecord]) -> dict[str, Any]:
        pnl_rows = [float(row.pnl_percent or 0.0) for row in rows if row.pnl_percent is not None]
        wins = sum(1 for pnl in pnl_rows if pnl > 0.0)
        losses = sum(1 for pnl in pnl_rows if pnl < 0.0)
        return {
            "trades": len(rows),
            "closed": len(pnl_rows),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round((wins / len(pnl_rows) * 100.0), 1) if pnl_rows else 0.0,
            "avg_pnl_pct": round(sum(pnl_rows) / len(pnl_rows), 3) if pnl_rows else 0.0,
        }

    shadow = _stats(groups["shadow"])
    real = _stats(groups["real"])
    return {
        "shadow": shadow,
        "real": real,
        "delta_closed": shadow["closed"] - real["closed"],
        "delta_win_rate_pct": round(shadow["win_rate_pct"] - real["win_rate_pct"], 1),
        "delta_avg_pnl_pct": round(shadow["avg_pnl_pct"] - real["avg_pnl_pct"], 3),
    }


def summarize_context_clusters(trades: list[TradeRecord]) -> dict[str, Any]:
    clusters: Counter[str] = Counter()
    symbol_clusters: Counter[str] = Counter()
    for trade in trades:
        regime = str(trade.market_regime or "UNKNOWN")
        side = str(trade.side or "?")
        outcome = "WIN" if (trade.pnl_percent or 0.0) > 0 else "LOSS_OR_FLAT"
        label = f"{regime}:{side}:{outcome}:{'SHADOW' if trade.is_shadow else 'REAL'}"
        clusters[label] += 1
        symbol_clusters[f"{trade.symbol}:{label}"] += 1
    return {
        "clusters": [{"label": key, "count": value} for key, value in clusters.most_common(15)],
        "symbol_clusters": [
            {"label": key, "count": value} for key, value in symbol_clusters.most_common(15)
        ],
    }

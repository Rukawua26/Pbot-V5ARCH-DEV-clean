from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from config import Config
from core.runtime_metrics import append_runtime_metric


def enabled() -> bool:
    return bool(getattr(Config, "SHADOW_VALIDATION_ENABLED", False))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def append_shadow_validation_event(event: str, payload: Mapping[str, Any] | None = None) -> None:
    if not enabled():
        return
    record = {
        "campaign": str(
            getattr(Config, "SHADOW_VALIDATION_CAMPAIGN", "shadow_macro_fvg_consensus_v1")
        ),
        "event": str(event),
    }
    if payload:
        record.update(dict(payload))
    append_runtime_metric("shadow_validation", record)


def emit_config_snapshot() -> None:
    append_shadow_validation_event(
        "config_snapshot",
        {
            "global_market_provider_enabled": bool(
                getattr(Config, "GLOBAL_MARKET_PROVIDER_ENABLED", False)
            ),
            "global_fear_greed_filter_enabled": bool(
                getattr(Config, "GLOBAL_FEAR_GREED_FILTER_ENABLED", True)
            ),
            "global_btc_dom_filter_enabled": bool(
                getattr(Config, "GLOBAL_BTC_DOM_FILTER_ENABLED", True)
            ),
            "global_fear_veto_threshold": _int(
                getattr(Config, "GLOBAL_FEAR_VETO_THRESHOLD", 20), 20
            ),
            "global_btc_dom_boost_threshold": _float(
                getattr(Config, "GLOBAL_BTC_DOM_BOOST_THRESHOLD", 65.0), 65.0
            ),
            "signal_agent_override_enabled": bool(
                getattr(Config, "SIGNAL_AGENT_OVERRIDE_ENABLED", True)
            ),
            "signal_agent_override_threshold": _float(
                getattr(Config, "SIGNAL_AGENT_OVERRIDE_THRESHOLD", 15.0), 15.0
            ),
            "fvg_tracker_enabled": bool(getattr(Config, "FVG_TRACKER_ENABLED", False)),
        },
    )


def emit_filter_decision(
    symbol: str,
    side: str,
    filter_passed: bool,
    filter_reason: str,
    prob_final: float,
    ctx: Mapping[str, Any] | None,
) -> None:
    ctx = ctx or {}
    append_shadow_validation_event(
        "filter_decision",
        {
            "symbol": symbol,
            "side": side,
            "filter_passed": bool(filter_passed),
            "filter_reason": str(filter_reason or ""),
            "prob_final": _float(prob_final),
            "fear_greed_index": _int(ctx.get("fear_greed_index"), 50),
            "btc_dominance": _float(ctx.get("btc_dominance")),
            "macro_boost_reason": str(ctx.get("macro_boost_reason") or ""),
            "agent_direction_score": _float(ctx.get("agent_direction_score")),
            "agent_signal_override": bool(ctx.get("agent_signal_override", False)),
            "base_trend": str(ctx.get("base_trend") or ""),
            "trend": str(ctx.get("trend") or ""),
            "ema_9": _float(ctx.get("ema_9")),
            "ema_21": _float(ctx.get("ema_21")),
            "ema_50": _float(ctx.get("ema")),
            "ema_fast_spread": _float(ctx.get("ema_fast_spread")),
            "ema_compression": _float(ctx.get("ema_compression")),
            "ema50_slope": _float(ctx.get("ema50_slope")),
        },
    )


def emit_shadow_trade_closed(
    trade: Mapping[str, Any],
    reason: str,
    exit_price: float,
    pnl_usd: float,
    pnl_percent: float,
    mae_percent: float,
    mfe_percent: float,
    exit_reason: str,
) -> None:
    if not bool(trade.get("is_shadow", False)):
        return
    snapshot = trade.get("market_snapshot") if isinstance(trade, Mapping) else {}
    if not isinstance(snapshot, Mapping):
        snapshot = {}
    append_shadow_validation_event(
        "shadow_trade_closed",
        {
            "symbol": str(trade.get("symbol") or ""),
            "side": str(trade.get("side") or ""),
            "reason": str(reason),
            "exit_reason": str(exit_reason or "UNKNOWN"),
            "entry": _float(trade.get("entry")),
            "exit": _float(exit_price),
            "pnl_usd": _float(pnl_usd),
            "pnl_percent": _float(pnl_percent),
            "mae_percent": _float(mae_percent),
            "mfe_percent": _float(mfe_percent),
            "entry_confidence": _float(trade.get("entry_confidence")),
            "market_regime": str(trade.get("market_regime") or snapshot.get("regime") or ""),
            "fear_greed_index": _int(snapshot.get("fear_greed_index"), 50),
            "btc_dominance": _float(snapshot.get("btc_dominance")),
            "agent_signal_override": bool(snapshot.get("agent_signal_override", False)),
            "agent_direction_score": _float(snapshot.get("agent_direction_score")),
        },
    )


def emit_fvg_cycle(symbols_scanned: int, new_gaps: int, active_gaps: list[dict]) -> None:
    status_counts: dict[str, int] = {}
    for gap in active_gaps:
        status = str(gap.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    append_shadow_validation_event(
        "fvg_cycle",
        {
            "symbols_scanned": int(symbols_scanned),
            "new_gaps": int(new_gaps),
            "active_total": len(active_gaps),
            "status_counts": status_counts,
        },
    )

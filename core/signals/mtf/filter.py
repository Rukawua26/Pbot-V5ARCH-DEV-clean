from __future__ import annotations

import pandas as pd

from config import Config
from core.execution_telemetry import append_execution_event
from core.signals.mtf.analyzer import analyze_mtf_alignment
from core.signals.mtf.data import fetch_mtf_data

# Rolling MTF metrics counters (module-level, reset on import)
_MTF_TOTAL_ATTEMPTS = 0
_MTF_VETOED_COUNT = 0
_MTF_VETO_REASONS: dict[str, int] = {}


def _log_mtf_metrics(bot) -> None:
    """Log aggregated MTF veto stats and reset counters."""
    global _MTF_TOTAL_ATTEMPTS, _MTF_VETOED_COUNT, _MTF_VETO_REASONS
    total = _MTF_TOTAL_ATTEMPTS
    vetoed = _MTF_VETOED_COUNT
    reasons = dict(_MTF_VETO_REASONS)
    _MTF_TOTAL_ATTEMPTS = 0
    _MTF_VETOED_COUNT = 0
    _MTF_VETO_REASONS = {}

    if total == 0:
        return

    veto_rate = (vetoed / total) * 100
    payload = {
        "window_total_attempts": total,
        "vetoed_count": vetoed,
        "veto_rate_pct": round(veto_rate, 2),
        "per_reason": reasons,
    }
    append_execution_event(bot, "MTF_VETO_STATS", payload)


def apply_mtf_filter(
    bot,
    symbol: str,
    signal: str,
    prob_final: float,
    ctx: dict,
    df_main: pd.DataFrame,
) -> tuple[float, bool, str]:
    global _MTF_TOTAL_ATTEMPTS, _MTF_VETOED_COUNT, _MTF_VETO_REASONS

    if not bool(getattr(Config, "MTF_FILTER_ENABLED", False)):
        return prob_final, True, "MTF_DISABLED"

    mtf_data = fetch_mtf_data(bot, symbol)
    market_regime = str(
        (ctx.get("btc_regime") if isinstance(ctx, dict) else None)
        or (ctx.get("regime") if isinstance(ctx, dict) else None)
        or getattr(bot, "market_regime", "")
        or ""
    )
    weight, reason = analyze_mtf_alignment(
        df_main,
        mtf_data.get("15m"),
        mtf_data.get("5m"),
        signal,
        regime=market_regime,
    )

    if isinstance(ctx, dict):
        ctx["mtf_weight"] = float(weight)
        ctx["mtf_reason"] = reason

    # Track metrics
    _MTF_TOTAL_ATTEMPTS += 1
    if weight <= 0.0:
        _MTF_VETOED_COUNT += 1
        _MTF_VETO_REASONS[reason] = _MTF_VETO_REASONS.get(reason, 0) + 1

    window = int(getattr(Config, "MTF_METRICS_WINDOW", 100))
    if _MTF_TOTAL_ATTEMPTS >= window:
        _log_mtf_metrics(bot)

    append_execution_event(
        bot,
        "MTF_FILTER",
        {
            "symbol": symbol,
            "side": signal,
            "weight": float(weight),
            "reason": reason,
            "prob_before": float(prob_final),
        },
    )

    if weight <= 0.0:
        return prob_final, False, f"MTF_VETO: {reason}"

    adjusted_prob = min(float(prob_final) * float(weight), 100.0)
    if adjusted_prob != prob_final:
        log = getattr(bot, "log", None)
        if callable(log):
            log(
                f"📊 {symbol}: MTF {reason} x{weight:.2f} "
                f"Prob {prob_final:.1f} → {adjusted_prob:.1f}"
            )
    return adjusted_prob, True, reason

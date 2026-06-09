from __future__ import annotations

from config import Config
from core.execution_telemetry import append_execution_event


def _cvd_direction(imbalance: float, threshold: float) -> str:
    if imbalance >= threshold:
        return "BUY"
    if imbalance <= -threshold:
        return "SELL"
    return "NEUTRAL"


def apply_cvd_filter(
    bot, symbol: str, signal: str, prob_final: float, ctx: dict
) -> tuple[float, bool, str]:
    """Adjust confidence using rolling CVD from aggTrade aggressor flow.

    This is intentionally conservative: CVD never hard-vetoes on its own. It
    only boosts aligned flow or penalizes conflicting flow while telemetry keeps
    the decision auditable.
    """
    if not bool(getattr(Config, "CVD_FILTER_ENABLED", False)):
        return prob_final, True, "CVD_DISABLED"

    signal = str(signal or "").upper()
    if signal not in {"BUY", "SELL"}:
        return prob_final, True, "CVD_PASSTHROUGH_UNSUPPORTED_SIGNAL"

    ws_manager = getattr(bot, "ws_manager", None)
    get_cvd_state = getattr(ws_manager, "get_cvd_state", None)
    if not callable(get_cvd_state):
        return prob_final, True, "CVD_PASSTHROUGH_NO_WS"

    state = get_cvd_state(symbol)
    if not isinstance(state, dict):
        return prob_final, True, "CVD_PASSTHROUGH_NO_DATA"

    total_volume = float(state.get("total_volume", 0.0) or 0.0)
    min_volume = float(getattr(Config, "CVD_MIN_QUOTE_VOLUME", 1000.0))
    if total_volume < min_volume:
        return prob_final, True, "CVD_PASSTHROUGH_LOW_VOLUME"

    imbalance = float(state.get("imbalance", 0.0) or 0.0)
    threshold = float(getattr(Config, "CVD_IMBALANCE_THRESHOLD", 0.12))
    direction = _cvd_direction(imbalance, threshold)
    aligned_weight = float(getattr(Config, "CVD_ALIGNED_WEIGHT", 1.05))
    conflict_weight = float(getattr(Config, "CVD_CONFLICT_WEIGHT", 0.85))

    if direction == signal:
        weight = aligned_weight
        reason = f"CVD_ALIGNED_{direction}"
    elif direction in {"BUY", "SELL"}:
        weight = conflict_weight
        reason = f"CVD_CONFLICT_{direction}_VS_{signal}"
    else:
        weight = 1.0
        reason = "CVD_NEUTRAL"

    adjusted_prob = min(float(prob_final) * float(weight), 100.0)
    if isinstance(ctx, dict):
        ctx["cvd_imbalance"] = imbalance
        ctx["cvd_direction"] = direction
        ctx["cvd_weight"] = float(weight)
        ctx["cvd_total_volume"] = total_volume

    append_execution_event(
        bot,
        "CVD_FILTER",
        {
            "symbol": symbol,
            "side": signal,
            "weight": float(weight),
            "reason": reason,
            "imbalance": imbalance,
            "total_volume": total_volume,
            "prob_before": float(prob_final),
            "prob_after": float(adjusted_prob),
        },
    )

    if adjusted_prob != prob_final:
        log = getattr(bot, "log", None)
        if callable(log):
            log(
                f"📊 {symbol}: CVD {reason} x{weight:.2f} "
                f"imb={imbalance:+.3f} Prob {prob_final:.1f} → {adjusted_prob:.1f}"
            )

    return adjusted_prob, True, reason

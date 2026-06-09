from __future__ import annotations

import pandas as pd


def _is_usable_df(df: pd.DataFrame | None, min_rows: int = 5) -> bool:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False
    return "close" in df.columns and len(df) >= min_rows


def _infer_direction(df: pd.DataFrame | None, min_candles: int = 20) -> str:
    if not _is_usable_df(df, min_rows=min_candles):
        return "UNKNOWN"
    assert df is not None
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < min_candles:
        return "UNKNOWN"

    first = float(closes.iloc[-min_candles])
    last = float(closes.iloc[-1])
    if first <= 0:
        return "UNKNOWN"

    from config import Config

    threshold = float(getattr(Config, "MTF_DIRECTION_THRESHOLD_PCT", 0.002))
    change_pct = (last - first) / first
    if change_pct >= threshold:
        return "BUY"
    if change_pct <= -threshold:
        return "SELL"
    return "NEUTRAL"


def _opposes_signal(direction: str, signal: str) -> bool:
    return (signal == "BUY" and direction == "SELL") or (signal == "SELL" and direction == "BUY")


def _confirms_signal(direction: str, signal: str) -> bool:
    return direction == signal


def _regime_confirms_signal(regime: str, signal: str) -> bool:
    """True when the macro regime directionally supports the signal.

    BULL_TREND confirms BUY; BEAR_TREND confirms SELL.
    RANGE and unknown regimes do not confirm either direction.
    """
    r = str(regime or "").upper().strip()
    s = str(signal or "").upper().strip()
    if s not in {"BUY", "SELL"}:
        return False
    if r == "BULL_TREND" and s == "BUY":
        return True
    if r == "BEAR_TREND" and s == "SELL":
        return True
    return False


def analyze_mtf_alignment(
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame | None,
    df_5m: pd.DataFrame | None,
    signal: str,
    regime: str = "",
) -> tuple[float, str]:
    """Evaluate 15m/5m alignment as confirmation for the 1h signal owner.

    The 15m timeframe can veto because it represents setup quality. The 5m
    timeframe only adjusts confidence because it is used as timing context.

    When the macro ``regime`` (BULL_TREND/BEAR_TREND) confirms the signal
    direction, a 15m opposition is treated as a pullback (dip in bull /
    bounce in bear) rather than a full veto — confidence is merely reduced.
    """
    signal = str(signal or "").upper()
    if signal not in {"BUY", "SELL"}:
        return 1.0, "MTF_PASSTHROUGH_UNSUPPORTED_SIGNAL"

    has_15m = _is_usable_df(df_15m)
    has_5m = _is_usable_df(df_5m)
    if not has_15m and not has_5m:
        return 1.0, "MTF_PASSTHROUGH_NO_INTRADAY_DATA"

    direction_15m = _infer_direction(df_15m)
    direction_5m = _infer_direction(df_5m)

    macro_confirms = _regime_confirms_signal(regime, signal)
    if _opposes_signal(direction_15m, signal):
        if macro_confirms:
            return 0.75, f"MTF_PULLBACK_15M_{direction_15m}_VS_{signal}"
        return 0.0, f"MTF_VETO_15M_{direction_15m}_VS_{signal}"

    if has_15m and direction_15m == "NEUTRAL":
        if has_5m:
            if _confirms_signal(direction_5m, signal):
                return 0.95, "MTF_PARTIAL_15M_NEUTRAL_5M_ALIGNED"
            if _opposes_signal(direction_5m, signal):
                return 0.60, "MTF_PARTIAL_15M_NEUTRAL_5M_CONFLICT"
        return 0.85, "MTF_PARTIAL_15M_NEUTRAL"

    confirms_15m = _confirms_signal(direction_15m, signal)
    confirms_5m = _confirms_signal(direction_5m, signal)

    if confirms_15m and confirms_5m:
        from config import Config

        boost = float(getattr(Config, "MTF_ALIGNED_BOOST", 1.0))
        return boost, "MTF_ALIGNED_15M_5M"

    if confirms_15m and _opposes_signal(direction_5m, signal):
        return 0.75, f"MTF_TIMING_5M_{direction_5m}_VS_{signal}"

    if confirms_15m:
        return 1.0, "MTF_ALIGNED_15M"

    if confirms_5m:
        return 0.90, "MTF_PARTIAL_5M_ONLY"

    return 1.0, "MTF_PASSTHROUGH_INCONCLUSIVE"

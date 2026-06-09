from __future__ import annotations

import numpy as np
import pandas as pd

from config import Config


def _pearson_correlation(prices_a: list[float], prices_b: list[float]) -> float:
    if len(prices_a) < 5 or len(prices_b) < 5:
        return 0.0
    min_len = min(len(prices_a), len(prices_b))
    a = np.array(prices_a[-min_len:], dtype=float)
    b = np.array(prices_b[-min_len:], dtype=float)
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    corr = np.corrcoef(a, b)[0, 1]
    return float(corr) if not np.isnan(corr) else 0.0


def _get_close_series(
    data_service, symbol: str, min_candles: int, window: int
) -> list[float] | None:
    try:
        df = data_service.fetch_and_update_data(symbol, "1h", fast_mode=True)
        if not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
            return None
        closes = pd.to_numeric(df["close"], errors="coerce").dropna().tolist()
        if len(closes) < min_candles:
            return None
        return closes[-window:]
    except Exception:
        return None


def compute_correlation_reduction(
    bot,
    candidate_symbol: str,
    open_symbols: list[str],
) -> tuple[float, list[dict]]:
    if not bool(getattr(Config, "CORRELATION_RISK_ENABLED", False)):
        return 1.0, []

    threshold = float(getattr(Config, "CORRELATION_RISK_THRESHOLD", 0.85))
    max_reduction = float(getattr(Config, "CORRELATION_RISK_REDUCTION_MAX", 0.50))
    window = int(getattr(Config, "CORRELATION_RISK_WINDOW", 48))
    min_candles = int(getattr(Config, "CORRELATION_RISK_MIN_CANDLES", 24))

    if not open_symbols:
        return 1.0, []

    data_service = getattr(bot, "data_service", None)
    if data_service is None:
        return 1.0, []

    candidate_closes = _get_close_series(data_service, candidate_symbol, min_candles, window)
    if candidate_closes is None:
        return 1.0, []

    correlations: list[dict] = []
    for sym in open_symbols:
        if sym == candidate_symbol:
            continue
        sym_closes = _get_close_series(data_service, sym, min_candles, window)
        if sym_closes is None:
            continue
        corr = _pearson_correlation(candidate_closes, sym_closes)
        correlations.append({"symbol": sym, "correlation": round(corr, 4)})

    if not correlations:
        return 1.0, []

    mean_corr = sum(item["correlation"] for item in correlations) / len(correlations)

    if mean_corr < threshold:
        return 1.0, correlations

    if mean_corr >= 1.0:
        reduction = max_reduction
    else:
        delta = mean_corr - threshold
        span = 1.0 - threshold
        reduction = 1.0 - ((1.0 - max_reduction) * delta / span)

    reduction = max(reduction, max_reduction)
    return round(reduction, 4), correlations

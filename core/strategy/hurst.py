import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("SniperAI")


def _log_returns(prices: np.ndarray) -> np.ndarray:
    arr = np.asarray(prices, dtype=float)
    if len(arr) < 2:
        return np.array([], dtype=float)
    return np.diff(np.log(arr + 1e-12))


class HurstEstimator:
    """Hurst exponent estimator via R/S analysis for market memory classification.
    Computed on log-returns for stationarity.

    H > 0.5 → Persistent (trending, memory)
    H = 0.5 → Random walk (no memory)
    H < 0.5 → Antipersistent (mean-reverting)
    """

    def __init__(self, window: int = 128, max_lag: int | None = None, min_lag: int = 10):
        self.window = int(window)
        self.max_lag = int(max_lag) if max_lag is not None else max(2, self.window // 2)
        self.min_lag = max(2, int(min_lag))
        self._last_value: float | None = None

    def compute(self, returns: np.ndarray) -> float:
        """Compute Hurst exponent via R/S analysis on a stationary series (returns).

        Returns H in [0, 1]. Returns 0.5 on failure (neutral assumption).
        """
        arr = np.asarray(returns, dtype=float)
        if len(arr) < self.min_lag * 2:
            return 0.5

        max_lag = min(self.max_lag, len(arr) // 2)
        if max_lag < self.min_lag:
            return 0.5

        lags = np.arange(self.min_lag, max_lag + 1, dtype=int)
        rs_values = np.empty(len(lags))

        for i, lag in enumerate(lags):
            n_segments = len(arr) // lag
            if n_segments < 2:
                rs_values[i] = np.nan
                continue

            segments = arr[: n_segments * lag].reshape(n_segments, lag)
            segment_rs = np.empty(n_segments)

            for j in range(n_segments):
                seg = segments[j]
                mean = np.mean(seg)
                deviations = seg - mean
                cumulative = np.cumsum(deviations)
                R = float(np.max(cumulative) - np.min(cumulative))
                S = float(np.std(seg, ddof=1))
                segment_rs[j] = R / S if S > 1e-12 else np.nan

            valid = segment_rs[~np.isnan(segment_rs)]
            rs_values[i] = float(np.mean(valid)) if len(valid) > 0 else np.nan

        valid_mask = ~np.isnan(rs_values)
        if np.sum(valid_mask) < 5:
            return 0.5

        log_lags = np.log(lags[valid_mask])
        log_rs = np.log(rs_values[valid_mask])

        A = np.vstack([log_lags, np.ones(len(log_lags))]).T
        try:
            H, _ = np.linalg.lstsq(A, log_rs, rcond=None)[0]
        except np.linalg.LinAlgError:
            return 0.5

        self._last_value = float(np.clip(H, 0.0, 1.0))
        return self._last_value

    def compute_from_df(self, df: pd.DataFrame, price_col: str = "close") -> float:
        """Compute Hurst on log-returns extracted from a price DataFrame."""
        if df is None or df.empty or price_col not in df.columns:
            return 0.5
        prices = df[price_col].values
        returns = _log_returns(prices)
        if len(returns) < self.min_lag * 2:
            return 0.5
        return self.compute(returns)

    def compute_rolling(self, df: pd.DataFrame, price_col: str = "close") -> pd.Series:
        """Compute rolling Hurst exponent on log-returns over a sliding window."""
        if df is None or df.empty or price_col not in df.columns:
            return pd.Series(dtype=float)

        prices = df[price_col].values
        returns = _log_returns(prices)
        result = np.full(len(prices), np.nan)

        for i in range(self.window, len(returns) + 1):
            chunk = returns[i - self.window : i]
            h = self.compute(chunk)
            result[i] = h

        return pd.Series(result, index=df.index, name="hurst")

    @staticmethod
    def classify(h: float) -> str:
        if h > 0.60:
            return "PERSISTENT"
        if h < 0.40:
            return "ANTIPERSISTENT"
        return "RANDOM_WALK"

    @staticmethod
    def classify_tolerant(h: float) -> str:
        """More permissive classification for weight adjustment (wider bands)."""
        if h > 0.55:
            return "PERSISTENT"
        if h < 0.45:
            return "ANTIPERSISTENT"
        return "RANDOM_WALK"

    @staticmethod
    def confidence(h: float) -> float:
        """Confidence from 0 (pure random) to 1 (strong memory)."""
        return min(1.0, abs(h - 0.5) * 4.0)

    @staticmethod
    def to_snapshot(h: float | None) -> dict[str, Any]:
        if h is None:
            return {
                "hurst": None,
                "class": "UNKNOWN",
                "confidence": 0.0,
                "ready": False,
            }
        return {
            "hurst": round(float(h), 4),
            "class": HurstEstimator.classify(h),
            "confidence": round(HurstEstimator.confidence(h), 4),
            "ready": True,
        }

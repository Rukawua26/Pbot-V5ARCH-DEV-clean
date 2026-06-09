from __future__ import annotations

import numpy as np
import pandas as pd


def next_shock_distance_pct(
    df: pd.DataFrame,
    side: str,
    pivot_window: int = 3,
    lookback_bars: int = 240,
) -> tuple[float | None, float | None]:
    """
    Estima distancia al proximo SHOCK usando pivots locales.

    - BUY: busca resistencia mas cercana por encima del precio actual.
    - SELL: busca soporte mas cercano por debajo del precio actual.

    Devuelve (dist_pct, level) o (None, None) si no hay nivel valido.
    """
    if df is None or df.empty:
        return None, None

    w = max(1, int(pivot_window))
    lookback = max((w * 2) + 5, int(lookback_bars))
    if len(df) < (w * 2 + 5):
        return None, None

    d = df.tail(lookback).reset_index(drop=True)
    current_price = float(d["close"].iloc[-1])
    if current_price <= 0:
        return None, None

    highs = d["high"].astype(float).values
    lows = d["low"].astype(float).values

    levels = []
    for i in range(w, len(d) - w):
        if side == "BUY":
            band = highs[i - w : i + w + 1]
            if highs[i] >= float(np.max(band)):
                levels.append(float(highs[i]))
        else:
            band = lows[i - w : i + w + 1]
            if lows[i] <= float(np.min(band)):
                levels.append(float(lows[i]))

    if not levels:
        return None, None

    if side == "BUY":
        candidates = [lvl for lvl in levels if lvl > current_price]
        if not candidates:
            return None, None
        target = min(candidates)
    else:
        candidates = [lvl for lvl in levels if lvl < current_price]
        if not candidates:
            return None, None
        target = max(candidates)

    dist_pct = abs(target - current_price) / current_price * 100.0
    return float(dist_pct), float(target)

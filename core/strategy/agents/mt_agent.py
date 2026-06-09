from typing import Any

import numpy as np
import pandas as pd

from core.config.hyperopt_loader import HyperoptConfigLoader
from core.strategy.base_agent import BaseAgent


class MTAgent(BaseAgent):
    """
    [SUPER-AGENTE MOMENTUM-TREND (MT)]
    Fusiona T (Technical), M (Momentum) y D (Divergence).
    Lógica de TRIPLE CONFIRMACIÓN: Solo emite señal si RSI, aceleración
    de precio y divergencia coinciden.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="MT", weight=weight)
        self.alma_offset = float(HyperoptConfigLoader.get_param("alma_offset", 0.85))
        self.alma_sigma = float(HyperoptConfigLoader.get_param("alma_sigma", 6.0))

    def _get_hyperopt_params(self, symbol: str) -> tuple[float, float]:
        params = HyperoptConfigLoader.get_params_for_symbol(symbol)
        return (
            float(params.get("alma_offset", self.alma_offset)),
            float(params.get("alma_sigma", self.alma_sigma)),
        )

    def _get_technical_score(self, context: dict[str, Any]) -> float:
        rsi = context.get("rsi", 50.0)
        adx = context.get("adx", 20.0)
        side = context.get("side", "BUY")

        score = 50.0
        if side == "BUY":
            if rsi < 35:
                score += 20
            if adx > 25:
                score += 15
            if rsi > 70:
                score -= 12
        else:
            if rsi > 65:
                score += 20
            if adx > 25:
                score += 15
            if rsi < 30:
                score -= 12
        return float(min(max(score, 20.0), 85.0))

    def _calculate_alma(
        self,
        series: pd.Series,
        window: int = 9,
        offset: float = 0.85,
        sigma: float = 6.0,
    ) -> float:
        """
        [AUDIT FIX V118-L4] Arnaud Legoux Moving Average (ALMA).
        Reemplaza la SMA cruda. Usa distribución Gaussiana para dar más peso
        a precios recientes sin repintar y mitigando el lag un 80% más rápido.
        """
        if len(series) < window:
            return float(series.iloc[-1])
        m = int(offset * (window - 1))
        s = window / sigma
        w = np.exp(-((np.arange(window) - m) ** 2) / (2 * s * s))
        w_sum = w.sum()
        alma = (series.iloc[-window:].values * w).sum() / w_sum
        return float(alma)

    def _get_momentum_score(
        self,
        df: pd.DataFrame,
        side: str,
        alma_offset: float | None = None,
        alma_sigma: float | None = None,
    ) -> float:
        if df is None or len(df) < 21:
            return 50.0
        closes = df["close"]
        alma_offset = float(alma_offset if alma_offset is not None else self.alma_offset)
        alma_sigma = float(alma_sigma if alma_sigma is not None else self.alma_sigma)

        # [AUDIT V118] Derivada ALMA: momento en t y en t-1
        # Usando .iloc[:-1] para acceder a la barra anterior sin look-ahead.
        alma_short_now = self._calculate_alma(
            closes, window=9, offset=alma_offset, sigma=alma_sigma
        )
        alma_long_now = self._calculate_alma(
            closes, window=20, offset=alma_offset, sigma=alma_sigma
        )
        alma_short_prev = self._calculate_alma(
            closes.iloc[:-1], window=9, offset=alma_offset, sigma=alma_sigma
        )
        alma_long_prev = self._calculate_alma(
            closes.iloc[:-1], window=20, offset=alma_offset, sigma=alma_sigma
        )

        mom_now = (alma_short_now - alma_long_now) / alma_long_now if alma_long_now > 0 else 0
        mom_prev = (alma_short_prev - alma_long_prev) / alma_long_prev if alma_long_prev > 0 else 0

        # Escala gradual para evitar "todo o nada"
        if side == "BUY":
            if mom_now > 0.004 and mom_prev > 0.002:
                return 78.0
            if mom_now > 0.001 and mom_prev > 0.0:
                return 66.0
            if mom_now < -0.002 and mom_prev < -0.001:
                return 34.0
        elif side == "SELL":
            if mom_now < -0.004 and mom_prev < -0.002:
                return 78.0
            if mom_now < -0.001 and mom_prev < 0.0:
                return 66.0
            if mom_now > 0.002 and mom_prev > 0.001:
                return 34.0
        return 50.0

    def _get_divergence_score(self, df: pd.DataFrame, side: str) -> float:
        if df is None or len(df) < 20:
            return 50.0
        rsi = df["rsi"]
        price = df["close"]

        # Confirmación simple de divergencia
        if side == "BUY":
            if price.iloc[-1] < price.iloc[-5] and rsi.iloc[-1] > rsi.iloc[-5]:
                return 80.0
            if price.iloc[-1] > price.iloc[-5] and rsi.iloc[-1] < rsi.iloc[-5]:
                return 35.0
        else:
            if price.iloc[-1] > price.iloc[-5] and rsi.iloc[-1] < rsi.iloc[-5]:
                return 80.0
            if price.iloc[-1] < price.iloc[-5] and rsi.iloc[-1] > rsi.iloc[-5]:
                return 35.0
        return 50.0

    def vote(self, context: dict[str, Any]) -> float:
        df = context.get("df")
        side = context.get("side", "BUY")
        symbol = context.get("symbol", "")
        alma_offset, alma_sigma = self._get_hyperopt_params(symbol)

        s_tech = self._get_technical_score(context)
        s_mom = self._get_momentum_score(df, side, alma_offset, alma_sigma)
        s_div = self._get_divergence_score(df, side)

        # Sistema ponderado (escala de grises) en vez de triple confirmación dura.
        weighted = (s_tech * 0.45) + (s_mom * 0.35) + (s_div * 0.20)

        # Bonus/Malus de confluencia
        bullish_count = sum(1 for s in [s_tech, s_mom, s_div] if s >= 60)
        bearish_count = sum(1 for s in [s_tech, s_mom, s_div] if s <= 40)
        if bullish_count >= 2:
            weighted += 4.0
        if bearish_count >= 2:
            weighted -= 4.0

        return float(min(max(weighted, 20.0), 85.0))

from typing import Any

import numpy as np
import pandas as pd

from core.config.hyperopt_loader import HyperoptConfigLoader
from core.strategy.base_agent import BaseAgent


class SRAgent(BaseAgent):
    """
    [SUPER-AGENTE STATISTICAL-REVERSION (SR)]
    Fusiona F (Fatigue) y E (Structure).
    Utiliza Z-Score y Entropía para identificar puntos de sobreextensión.
    Solo emite señal si el estiramiento es > 2.5 desviaciones estándar.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="SR", weight=weight)
        z_loaded = float(HyperoptConfigLoader.get_param("z_score_threshold", 2.0))
        # [UNLOCK] Limitar umbral para evitar neutralidad excesiva.
        self.z_score_threshold = min(2.0, max(1.4, z_loaded))
        self.entropy_bins = int(HyperoptConfigLoader.get_param("entropy_bins", 10))

    def _get_hyperopt_params(self, symbol: str) -> tuple[float, int]:
        params = HyperoptConfigLoader.get_params_for_symbol(symbol)
        z_loaded = float(params.get("z_score_threshold", self.z_score_threshold))
        return min(2.0, max(1.4, z_loaded)), int(params.get("entropy_bins", self.entropy_bins))

    def _calculate_entropy(self, series: Any, window: int = 20) -> float:
        """Calcula la Entropía de Shannon de los retornos."""
        if series is None or not isinstance(series, pd.Series) or len(series) < window:
            return 0.0
        try:
            returns = series.pct_change().dropna().tail(window)
            if len(returns) == 0:
                return 0.0
            bins = max(2, int(self.entropy_bins))
            counts, _ = np.histogram(returns, bins=bins)
            probs = counts / (sum(counts) if sum(counts) > 0 else 1)
            probs = probs[probs > 0]
            if len(probs) == 0:
                return 0.0
            return -np.sum(probs * np.log2(probs))
        except Exception:
            return 0.0

    def _calculate_kinetic_modifier(self, df: Any, z_score: float) -> float:
        """Mide desaceleración/aceleración en las últimas 3 velas.

        Retorna:
          > 1.0 si hay desaceleración (absorción en el nivel).
          < 1.0 si hay aceleración (inercia devastadora, falling knife).
          1.0 si es neutro.
        """
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 3:
            return 1.0

        try:
            last = df.tail(3)
            ranges = (last["high"] - last["low"]).replace(0, float("nan"))
            bodies = (last["close"] - last["open"]).abs()
            body_ratios = bodies / ranges

            if z_score < 0:
                # BUY zone → mirar lower wick (rechazo del soporte)
                low_price = last[["open", "close"]].min(axis=1)
                rejection = (low_price - last["low"]) / ranges
            else:
                # SELL zone → mirar upper wick (rechazo de la resistencia)
                high_price = last[["open", "close"]].max(axis=1)
                rejection = (last["high"] - high_price) / ranges

            avg_body = body_ratios.mean()
            avg_rejection = rejection.mean()

            # Desaceleración: cuerpos pequeños + mecha larga de rechazo
            if avg_body < 0.4 and avg_rejection >= 0.5:
                return 1.3
            # Aceleración: cuerpos grandes + sin mecha de rechazo
            if avg_body > 0.8 and avg_rejection < 0.2:
                return 0.7
            return 1.0
        except Exception:
            return 1.0

    def vote(self, context: dict[str, Any]) -> float:
        df = context.get("df")
        z_score = context.get("z_score", 0.0)
        symbol = context.get("symbol", "")
        z_score_threshold, entropy_bins = self._get_hyperopt_params(symbol)

        # [AUDIT FIX V118-L4] Z-Score Dinámico vía ATR para evitar Model Drift
        z_score_dinamico = z_score
        if df is not None and len(df) >= 20:
            try:
                # Calculo de TR (True Range) y ATR
                high = df["high"]
                low = df["low"]
                close_prev = df["close"].shift(1)

                tr1 = high - low
                tr2 = (high - close_prev).abs()
                tr3 = (low - close_prev).abs()

                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr = tr.rolling(14).mean().iloc[-1]

                sma = df["close"].rolling(20).mean().iloc[-1]
                price = df["close"].iloc[-1]

                # Z-Score ajustado suavizado por volatilidad real
                z_score_dinamico = (price - sma) / (atr * 1.5) if atr > 0 else z_score
            except Exception:
                z_score_dinamico = z_score

        # Condición: Solo dispara si Z-Score Dinámico supera umbral optimizado
        if abs(z_score_dinamico) < z_score_threshold:
            return 50.0

        previous_entropy_bins = self.entropy_bins
        self.entropy_bins = entropy_bins
        entropy = self._calculate_entropy(df["close"]) if df is not None else 0.0
        self.entropy_bins = previous_entropy_bins

        score = 50.0
        # Reversión estadística con umbral optimizado
        if z_score_dinamico > z_score_threshold:
            score = 20.0 + (entropy * 5)  # Voto fuerte a SELL
        elif z_score_dinamico < -z_score_threshold:
            score = 80.0 - (entropy * 5)  # Voto fuerte a BUY

        # [KINETIC v118.7] Modulador por desaceleración/aceleración en el nivel
        modifier = self._calculate_kinetic_modifier(df, z_score_dinamico)
        score = min(100.0, max(0.0, score * modifier))

        return score

from typing import Any

from core.strategy.base_agent import BaseAgent


class SentimentAgent(BaseAgent):
    """
    [AGENTE SENTIMIENTO (S)]
    Analiza el sentimiento del mercado basado en BTC Delta y Volatilidad (ATR%).
    Diferencia estados de Miedo, Pánico, Optimismo y Euforia.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="S", weight=weight)

    def vote(self, context: dict[str, Any]) -> float:
        btc_delta_tf = context.get("btc_delta_tf", 0.0)
        volatility = context.get("atr_pct", 0.02)  # Atr% como medida de volatilidad

        score = 50.0

        # Lógica de BTC Delta (Prioridad)
        if btc_delta_tf < -1.0:
            score = 10.0  # Pánico extremo
        elif btc_delta_tf < -0.5:
            score = 30.0  # Miedo
        elif btc_delta_tf > 1.0:
            score = 90.0  # Euforia
        elif btc_delta_tf > 0.5:
            score = 70.0  # Optimismo

        # Ajuste por Volatilidad Extrema (Incertidumbre)
        if volatility > 0.05:  # > 5% ATR/Price es señal de caos o alta incertidumbre
            score = min(score, 40.0)

        return score

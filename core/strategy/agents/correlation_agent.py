from typing import Any

from core.strategy.base_agent import BaseAgent


class CorrelationAgent(BaseAgent):
    """
    [AGENTE CORRELACIÓN (C)]
    Evalúa si el trade va a favor del movimiento de BTC.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="C", weight=weight)

    def vote(self, context: dict[str, Any]) -> float:
        side = context.get("side", context.get("signal", "BUY"))
        btc_delta_tf = context.get("btc_delta_tf", 0.0)

        score = 50.0
        if side == "BUY":
            if btc_delta_tf > 0:
                score += 15
            elif btc_delta_tf < -0.5:
                score -= 20
        elif side == "SELL":
            if btc_delta_tf < 0:
                score += 15
            elif btc_delta_tf > 0.5:
                score -= 20

        return min(max(score, 0), 100.0)

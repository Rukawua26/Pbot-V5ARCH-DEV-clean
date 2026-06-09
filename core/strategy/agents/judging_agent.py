from typing import Any

from core.strategy.base_agent import BaseAgent


class JudgingAgent(BaseAgent):
    """
    [AGENTE JUDGING (J)]
    Utiliza el motor de rendimiento contextual (Brain) para puntuar la señal
    basándose en el éxito histórico en condiciones similares.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="J", weight=weight)

    def vote(self, context: dict[str, Any]) -> float:
        brain = context.get("brain_instance")
        symbol = context.get("symbol", "Asset")
        rsi = context.get("rsi", 50.0)
        adx = context.get("adx", 20.0)

        if brain is None:
            return 50.0

        try:
            # El brain calcula internamente el score basado en el rendimiento histórico contextual
            score = brain.get_contextual_performance_score(symbol, rsi, adx)
            return float(score)
        except Exception:
            return 50.0

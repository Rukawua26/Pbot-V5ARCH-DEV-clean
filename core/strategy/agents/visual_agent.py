from typing import Any

from core.strategy.base_agent import BaseAgent


class VisualAgent(BaseAgent):
    """
    [AGENTE VISUAL (V)]
    Analiza la forma de las velas (Hammer, Shooting Star) para detectar reversals.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="V", weight=weight)

    def vote(self, context: dict[str, Any]) -> float:
        df = context.get("df")
        side = context.get("side", "BUY")

        if df is None or len(df) < 3:
            return 50.0

        score = 50.0
        try:
            # Última vela cerrada (índice -2 en tiempo real)
            vela = df.iloc[-2]
            cuerpo = abs(vela["close"] - vela["open"])
            mecha_sup = vela["high"] - max(vela["close"], vela["open"])
            mecha_inf = min(vela["close"], vela["open"]) - vela["low"]
            rango_total = vela["high"] - vela["low"]

            if rango_total == 0:
                return 50.0

            # Detección de Patrones Visuales
            if mecha_sup > cuerpo * 2 and mecha_inf < cuerpo * 0.5:
                # Shooting Star (Bajista)
                if side == "SELL":
                    score += 20.0
                elif side == "BUY":
                    score -= 25.0  # Trampa alcista (Bull Trap)

            elif mecha_inf > cuerpo * 2 and mecha_sup < cuerpo * 0.5:
                # Hammer (Alcista)
                if side == "BUY":
                    score += 20.0
                elif side == "SELL":
                    score -= 25.0  # Trampa bajista (Bear Trap)

        except Exception:
            pass

        return min(max(score, 0), 100)

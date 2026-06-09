from core.strategy.base_agent import BaseAgent

try:
    from advanced_ensemble import OrderBookAnalyzer

    OB_AVAILABLE = True
except ImportError:
    OB_AVAILABLE = False


class LBAgent(BaseAgent):
    """
    [SUPER-AGENTE LIQUIDITY-BOOKS (LB)]
    Fusiona L (Liquidity) y K (Whale).
    Analiza desequilibrio del Order Book y muros de ballenas (vol_rel).
    Solo vota si el desequilibrio y el volumen coinciden (voto real vs fake).
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="LB", weight=weight)
        self._vol_history = {}  # [AUDIT FIX V118-L4] Anti-Spoofing Memoria Temporal

    def vote(self, context: dict) -> float:
        order_book = context.get("order_book")
        vol_rel = context.get("vol_rel", 1.0)
        side = context.get("side", "BUY")
        symbol = context.get("symbol", "UNKNOWN")

        if not order_book:
            return 50.0

        # 1. Desequilibrio de Libro
        ob_score = 0.5
        if OB_AVAILABLE:
            ob_score, _ = OrderBookAnalyzer.analyze(order_book, side.lower())
        else:
            bids = sum(b[1] for b in order_book.get("bids", [])[:5])
            asks = sum(a[1] for a in order_book.get("asks", [])[:5])
            if (bids + asks) > 0:
                ob_score = bids / (bids + asks) if side == "BUY" else asks / (bids + asks)

        # 2. Confirmación con Ballenas (Volumen masivo) y ANTI-SPOOFING
        history = self._vol_history.get(symbol, [])
        history.append(vol_rel)
        if len(history) > 5:
            history.pop(0)
        self._vol_history[symbol] = history

        avg_vol = sum(history[-3:]) / min(len(history), 3) if history else vol_rel

        # Solo votamos si la media reciente y actual supera 1.5
        if avg_vol < 1.5 or vol_rel < 1.2:
            return 50.0

        # 3. [AUDIT V118] Filtro de Order Flow: Tick Count Anti-Spoofing
        # Si el contexto provee tick_count y tick_count_avg, verificamos que el muro
        # de ballena sea acompañado por operaciones reales y no sea un flash-wall.
        tick_count = context.get("tick_count", 0)
        tick_avg = context.get("tick_count_avg", 0)
        if tick_count > 0 and tick_avg > 0:
            if tick_count < tick_avg * 1.2:
                # Muro sin flujo real → probable spoofing, abstención conservadora
                return 50.0

        score = ob_score * 100

        # Ajuste final por potencia de la ballena sólida.
        if avg_vol > 3.0:
            score = min(score + 20, 95.0) if score > 50 else max(score - 20, 5.0)

        return min(max(score, 0), 100)

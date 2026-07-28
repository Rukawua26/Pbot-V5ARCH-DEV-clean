"""Validaciones defensivas de ACK de órdenes protectoras (HARD SL).

El exchange es la fuente de verdad para exposición real, pero la respuesta de
`create_order` puede cancelarse, rechazarse o llegar incompleta. Aceptarla sin
verificar deja una posición registrada como protegida cuando no lo está.

Estas funciones validan el ACK devuelto por `place_hard_sl` contra el contexto
esperado (símbolo, lado, cantidad). No validan `status=='open'` porque Binance
puede tardar en propagar el estado; en cambio, descartan ACKs claramente
inválidos (campos faltantes, lado opuesto al esperado, símbolo distinto, o
identificadores nulos). Ante ambigüedad, el caller debe aplicar fail-safe/HALT
siguiendo el invariante "ante estado live ambiguo, preferir HALT".
"""

from __future__ import annotations

from typing import Any


def hard_sl_ack_looks_valid(
    sl_order: Any,
    *,
    expected_symbol: str,
    expected_sl_side: str,
    expected_amount: float,
    tolerance: float = 0.0001,
) -> tuple[bool, str]:
    """Valida que un ACK de HARD SL sea estructuralmente plausible.

    Devuelve (ok, reason). `ok=False` obliga al caller a tratar la posición
    como desprotegida (fail-safe close + HALT) y nunca a confiar en el ACK.
    """
    if not isinstance(sl_order, dict):
        return False, "HARD_SL_ACK_NOT_DICT"
    order_id = sl_order.get("id")
    if not order_id:
        return False, "HARD_SL_ACK_MISSING_ID"
    if not sl_order.get("symbol"):
        return False, "HARD_SL_ACK_MISSING_SYMBOL"
    ack_symbol = str(sl_order.get("symbol"))
    if ack_symbol != expected_symbol:
        return False, f"HARD_SL_ACK_SYMBOL_MISMATCH:{ack_symbol}:{expected_symbol}"
    ack_side = str(sl_order.get("side") or "").lower()
    if not ack_side:
        return False, "HARD_SL_ACK_MISSING_SIDE"
    if ack_side != expected_sl_side.lower():
        return False, f"HARD_SL_ACK_SIDE_MISMATCH:{ack_side}:{expected_sl_side}"
    info = sl_order.get("info") or {}
    reduce_only_field = info.get("reduceOnly")
    if reduce_only_field is False:
        return False, "HARD_SL_ACK_NOT_REDUCE_ONLY"
    ack_amount = sl_order.get("amount") or sl_order.get("filled")
    if ack_amount is not None:
        try:
            diff = abs(float(ack_amount) - float(expected_amount))
            base = max(abs(float(expected_amount)), 1.0)
            if diff / base > tolerance:
                return False, f"HARD_SL_ACK_AMOUNT_MISMATCH:{ack_amount}:{expected_amount}"
        except (TypeError, ValueError):
            return False, "HARD_SL_ACK_AMOUNT_UNPARSEABLE"
    return True, ""


def sl_side_for_trade_side(trade_side: str) -> str:
    """Lado del STOP_MARKET: cerrar un BUY requiere SELL, y viceversa."""
    return "sell" if str(trade_side).lower() == "buy" else "buy"

"""Validaciones defensivas de ACK de órdenes protectoras (HARD SL).

El exchange es la fuente de verdad para exposición real, pero la respuesta de
`create_order` puede cancelarse, rechazarse o llegar incompleta. Aceptarla sin
verificar deja una posición registrada como protegida cuando no lo está.

Estas funciones validan el ACK devuelto por `place_hard_sl` contra el contexto
esperado (símbolo, lado, cantidad y estado activo). Ante ambigüedad, el caller
debe aplicar fail-safe/HALT siguiendo el invariante "ante estado live ambiguo,
preferir HALT".
"""

from __future__ import annotations

import math
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
    status = str(sl_order.get("status") or "").lower()
    if status not in {"open", "new"}:
        return False, f"HARD_SL_ACK_NOT_ACTIVE:{status or 'missing'}"
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
    raw_info = sl_order.get("info")
    info = raw_info if isinstance(raw_info, dict) else {}
    order_type = str(sl_order.get("type") or info.get("type") or "").lower()
    if order_type.replace("-", "_") not in {"stop_market", "stopmarket"}:
        return False, f"HARD_SL_ACK_INVALID_TYPE:{order_type or 'missing'}"
    reduce_only_field = sl_order.get("reduceOnly", info.get("reduceOnly"))
    reduce_only = reduce_only_field is True or (
        isinstance(reduce_only_field, str) and reduce_only_field.lower() == "true"
    )
    if not reduce_only:
        return False, "HARD_SL_ACK_NOT_REDUCE_ONLY"
    ack_amount = sl_order.get("amount")
    if ack_amount is None:
        ack_amount = sl_order.get("filled")
    if ack_amount is None or isinstance(ack_amount, bool):
        return False, "HARD_SL_ACK_AMOUNT_UNPARSEABLE"
    try:
        parsed_amount = float(ack_amount)
        parsed_expected = float(expected_amount)
        if not math.isfinite(parsed_amount) or parsed_amount <= 0:
            return False, "HARD_SL_ACK_AMOUNT_UNPARSEABLE"
        if not math.isfinite(parsed_expected) or parsed_expected <= 0:
            return False, "HARD_SL_EXPECTED_AMOUNT_INVALID"
        diff = abs(parsed_amount - parsed_expected)
        if diff / abs(parsed_expected) > tolerance:
            return False, f"HARD_SL_ACK_AMOUNT_MISMATCH:{ack_amount}:{expected_amount}"
    except (TypeError, ValueError):
        return False, "HARD_SL_ACK_AMOUNT_UNPARSEABLE"
    return True, ""


def sl_side_for_trade_side(trade_side: str) -> str:
    """Lado del STOP_MARKET: cerrar un BUY requiere SELL, y viceversa."""
    return "sell" if str(trade_side).lower() == "buy" else "buy"

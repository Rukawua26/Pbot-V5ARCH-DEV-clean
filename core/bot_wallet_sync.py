from datetime import datetime

from config import Config
from core.execution_safety import hard_sl_ack_looks_valid, sl_side_for_trade_side
from core.execution_telemetry import append_execution_event
from core.reconciliation import generate_child_client_order_id, generate_order_ids
from core.symbol_utils import normalize_position_symbol
from core.time_utils import parse_datetime_utc, utc_now
from core.trade_helpers import _emergency_market_close
from core.trade_keys import find_trade_key, make_trade_key, normalize_trade_side
from tools.notifier import send_telegram_msg

_OPEN_ENTRY_ORDER_LOOKUP_FAILED = object()
_HARD_SL_ORDER_LOOKUP_FAILED = object()


def _bool_reduce_only(order: dict) -> bool:
    info = order.get("info") or {}
    raw = info.get("reduceOnly", order.get("reduceOnly", False))
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"true", "1", "yes"}


def _order_amount(order: dict) -> float:
    info = order.get("info") or {}
    for key in ("amount", "origQty", "quantity", "qty"):
        value = order.get(key, info.get(key))
        if value is not None:
            try:
                return abs(float(value or 0.0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _order_client_id(order: dict) -> str:
    info = order.get("info") or {}
    return str(order.get("clientOrderId") or info.get("clientOrderId") or "")


def _is_protective_stop_for_trade(order: dict, trade: dict) -> bool:
    order_type = str(order.get("type") or (order.get("info") or {}).get("type") or "").upper()
    if "STOP" not in order_type:
        return False
    if not _bool_reduce_only(order):
        return False
    order_side = str(order.get("side") or "").upper()
    trade_side = str(trade.get("side") or "").upper()
    if trade_side == "BUY":
        return order_side == "SELL"
    if trade_side == "SELL":
        return order_side == "BUY"
    return False


def _hard_sl_order_covers_trade(order: dict, trade: dict, info: dict | None = None) -> bool:
    if not _is_protective_stop_for_trade(order, trade):
        return False

    order_id = str(order.get("id") or "")
    known_order_id = str(trade.get("sl_exchange_order_id") or "")
    if known_order_id and order_id and order_id != known_order_id:
        return False

    known_client_id = str(trade.get("sl_client_order_id") or "")
    if known_client_id and _order_client_id(order) and _order_client_id(order) != known_client_id:
        return False

    order_info = order.get("info") or {}
    close_position_raw = order.get("closePosition", order_info.get("closePosition", False))
    close_position = str(close_position_raw).lower() in {"true", "1", "yes"}
    if close_position:
        return True

    trade_amount = float((info or {}).get("amount") or trade.get("amount") or 0.0)
    if trade_amount <= 0:
        return False
    return _order_amount(order) >= trade_amount * 0.999


def _find_existing_hard_sl_order(bot, symbol: str, trade: dict):
    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    if not callable(fetch_open_orders):
        return None
    try:
        open_orders = fetch_open_orders(symbol) or []
        orders_iter = open_orders if isinstance(open_orders, (list, tuple)) else []
        for order in orders_iter:
            if _hard_sl_order_covers_trade(order, trade):
                return order
    except Exception as error:
        bot.log(f"⚠️ No se pudo inspeccionar open orders de {symbol} para SL: {error}")
        return _HARD_SL_ORDER_LOOKUP_FAILED
    return None


def _find_verified_hard_sl_order(bot, symbol: str, trade: dict, info: dict):
    if Config.PAPER_MODE or trade.get("is_shadow", False):
        return None
    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    if not callable(fetch_open_orders):
        return None
    try:
        open_orders = fetch_open_orders(symbol) or []
    except Exception as error:
        bot.is_paused = True
        bot.integrity_lock_active = True
        setattr(bot, "halt_system_active", True)
        with bot.db_lock:
            bot.brain.save_error_snapshot(symbol, "SL_VERIFY_FAILED_HALT", {"error": str(error)})
        bot.log(f"🛑 SL_VERIFY_FAILED_HALT {symbol}: {error}. No se pudo verificar HARD SL.")
        append_execution_event(
            bot,
            "SL_VERIFY_FAILED_HALT",
            {"symbol": symbol, "error": str(error)},
        )
        return "HALT"
    orders_iter = open_orders if isinstance(open_orders, (list, tuple)) else []
    for order in orders_iter:
        if _hard_sl_order_covers_trade(order, trade, info):
            return order
    return None


def _halt_ambiguous_hard_sl_visibility(bot, symbol: str, trade: dict) -> None:
    trade["status"] = "HARD_SL_VISIBILITY_AMBIGUOUS"
    with bot.db_lock:
        bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
    _halt_wallet_sync(
        bot,
        "HARD_SL_VISIBILITY_AMBIGUOUS",
        {"symbol": symbol, "sl_exchange_order_id": trade.get("sl_exchange_order_id")},
    )


def _is_immediate_trigger_rejection(error_text: str) -> bool:
    msg = str(error_text or "").lower()
    return (
        "trigger immediately" in msg
        or "would immediately trigger" in msg
        or "order would trigger" in msg
        or "-2021" in msg
    )


def _exchange_position_is_flat(bot, symbol: str) -> bool:
    fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
    if not callable(fetch_positions):
        return False
    positions = fetch_positions() or []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if normalize_position_symbol(pos.get("symbol", "")) != symbol:
            continue
        amount = pos.get("contracts")
        if amount is None:
            amount = (pos.get("info") or {}).get("positionAmt", 0)
        if abs(float(amount or 0.0)) > 0.0:
            return False
    return True


def _halt_wallet_sync(bot, reason: str, details: dict | None = None) -> None:
    bot.is_paused = True
    bot.integrity_lock_active = True
    setattr(bot, "halt_system_active", True)
    payload = dict(details or {})
    payload["reason"] = reason
    with bot.db_lock:
        bot.brain.save_error_snapshot("SYSTEM", "WALLET_SYNC_HALT", payload)
    append_execution_event(bot, "WALLET_SYNC_HALT", payload)
    bot.log(f"🛑 WALLET_SYNC_HALT: {reason}")


def _emergency_market_close_unprotected(
    bot, symbol: str, trade: dict, amount: float, sl_error: str
):
    side = str(trade.get("side") or "BUY")
    _emergency_market_close(
        bot=bot,
        symbol=symbol,
        side=side,
        amount=amount,
        verify_flat=True,
        persist_state=True,
        halt_on_failure=True,
        trade=trade,
        sl_error=sl_error,
        append_event_fn=append_execution_event,
        send_telegram_fn=send_telegram_msg,
    )


def _ensure_hard_sl_attached(bot, symbol: str, trade: dict, info: dict):
    if trade.get("is_shadow") or Config.PAPER_MODE:
        return False
    if trade.get("sl_exchange_order_id"):
        verified_sl = _find_verified_hard_sl_order(bot, symbol, trade, info)
        if verified_sl and verified_sl != "HALT":
            return False
        if verified_sl == "HALT":
            bot.log(
                f"🛑 HARD_SL_VERIFICATION_HALTED {symbol}: wallet sync aborted due to verification failure"
            )
            return False
        bot.log(
            f"🛑 HARD_SL_VISIBILITY_AMBIGUOUS {symbol}: id local "
            f"{trade.get('sl_exchange_order_id')} no se verificó en open orders."
        )
        _halt_ambiguous_hard_sl_visibility(bot, symbol, trade)
        return False

    existing_sl = _find_existing_hard_sl_order(bot, symbol, trade)
    if existing_sl is _HARD_SL_ORDER_LOOKUP_FAILED:
        _halt_wallet_sync(
            bot,
            "HARD_SL_OPEN_ORDERS_LOOKUP_FAILED",
            {"symbol": symbol},
        )
        return False
    if existing_sl:
        trade["sl_exchange_order_id"] = existing_sl.get("id")
        trade["status"] = "OPEN"
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        bot.log(f"🛡️ SL existente detectado para {symbol}: {existing_sl.get('id')}")
        return False

    sl_price = float(trade.get("sl") or 0.0)
    if sl_price <= 0:
        entry = float(trade.get("entry") or info.get("entry") or 0.0)
        side = str(trade.get("side") or info.get("side") or "BUY")
        sl_price = entry * (0.995 if side == "BUY" else 1.005)
        trade["sl"] = sl_price

    amount = float(info.get("amount") or trade.get("amount") or 0.0)
    if amount <= 0:
        return False

    entry_coid = str(trade.get("entry_client_order_id") or "")
    sl_coid = str(trade.get("sl_client_order_id") or "")
    if not sl_coid and entry_coid:
        sl_coid = generate_child_client_order_id(entry_coid, "SL")
        trade["sl_client_order_id"] = sl_coid

    sl_side_str = str(trade.get("side") or info.get("side") or "BUY")
    hedge_position_side = (
        ("LONG" if sl_side_str.upper() == "BUY" else "SHORT")
        if bool(getattr(bot, "is_hedge_mode", False))
        else None
    )
    sl_order = bot.execution.place_hard_sl(
        symbol,
        sl_side_str,
        amount,
        sl_price,
        client_order_id=sl_coid or None,
        params={"positionSide": hedge_position_side} if hedge_position_side else None,
    )
    sl_ack_ok, sl_ack_reason = hard_sl_ack_looks_valid(
        sl_order,
        expected_symbol=symbol,
        expected_sl_side=sl_side_for_trade_side(sl_side_str),
        expected_amount=amount,
    )
    if sl_order and sl_ack_ok:
        trade["sl_exchange_order_id"] = sl_order.get("id")
        trade["hard_sl_attach_fail_count"] = 0
        trade["status"] = "OPEN"
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        bot.log(f"🛡️ HARD SL recuperado para {symbol}: {sl_order.get('id')}")
    else:
        if sl_order is not None:
            sl_error = sl_ack_reason or str(getattr(bot.execution, "last_hard_sl_error", "") or "")
        else:
            sl_error = str(getattr(bot.execution, "last_hard_sl_error", "") or "")
        if _is_immediate_trigger_rejection(sl_error):
            _emergency_market_close_unprotected(bot, symbol, trade, amount, sl_error)
            return True
        fail_count = int(trade.get("hard_sl_attach_fail_count") or 0) + 1
        trade["hard_sl_attach_fail_count"] = fail_count
        trade["status"] = "HARD_SL_UNPROTECTED"
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        max_retries = int(getattr(Config, "HARD_SL_ATTACH_MAX_RETRIES", 3) or 3)
        if fail_count >= max_retries:
            bot.log(
                f"☢️ HARD_SL_ATTACH_RETRY_EXHAUSTED {symbol}: {fail_count}/{max_retries}. Ejecutando cierre de emergencia."
            )
            _emergency_market_close_unprotected(
                bot,
                symbol,
                trade,
                amount,
                sl_error or "HARD_SL_ATTACH_FAILED_PERSISTENT",
            )
            return True
        _halt_wallet_sync(
            bot,
            "HARD_SL_ATTACH_FAILED_UNPROTECTED",
            {"symbol": symbol, "fail_count": fail_count, "sl_error": sl_error[:180]},
        )
        bot.log(f"⚠️ Riesgo crítico: {symbol} sigue sin HARD SL en exchange")
        return True
    return False


def _parse_iso_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _find_open_entry_order(bot, symbol: str, trade: dict):
    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    if not callable(fetch_open_orders):
        return None
    try:
        open_orders = fetch_open_orders(symbol) or []
    except Exception as error:
        bot.log(f"⚠️ No se pudo inspeccionar orden entry abierta de {symbol}: {error}")
        append_execution_event(
            bot,
            "PARTIAL_FILL_ENTRY_LOOKUP_FAILED",
            {"symbol": symbol, "error": str(error)[:180]},
        )
        return _OPEN_ENTRY_ORDER_LOOKUP_FAILED
    orders_iter = open_orders if isinstance(open_orders, (list, tuple)) else []
    entry_order_id = str(trade.get("entry_exchange_order_id") or "")
    entry_coid = str(trade.get("entry_client_order_id") or "")
    for order in orders_iter:
        if not isinstance(order, dict):
            continue
        order_id = str(order.get("id") or "")
        coid = str(
            order.get("clientOrderId") or (order.get("info") or {}).get("clientOrderId") or ""
        )
        if entry_order_id and order_id == entry_order_id:
            return order
        if entry_coid and coid == entry_coid:
            return order
    return None


def _manage_partial_fill_trade(bot, symbol: str, trade: dict, info: dict):
    if not trade.get("partial_fill_pending") and trade.get("status") != "PARTIAL_FILL_PENDING":
        return

    executed_amount = float(info.get("amount") or trade.get("amount") or 0.0)
    if executed_amount <= 0:
        return
    requested_amount = float(trade.get("requested_amount") or executed_amount)
    if requested_amount < executed_amount:
        requested_amount = executed_amount

    trade["amount"] = executed_amount
    trade["requested_amount"] = requested_amount
    trade["entry"] = float(info.get("entry") or trade.get("entry") or 0.0)
    remaining_estimated = max(0.0, requested_amount - executed_amount)

    open_entry_order = _find_open_entry_order(bot, symbol, trade)
    if open_entry_order is _OPEN_ENTRY_ORDER_LOOKUP_FAILED:
        trade["remaining_amount"] = remaining_estimated
        trade["status"] = "PARTIAL_FILL_PENDING"
        trade["partial_fill_pending"] = True
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        return

    if open_entry_order is None:
        trade["remaining_amount"] = 0.0
        trade["partial_fill_pending"] = False
        trade["status"] = "OPEN"
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        append_execution_event(
            bot,
            "PARTIAL_FILL_COMPLETED",
            {
                "symbol": symbol,
                "requested_amount": requested_amount,
                "executed_amount": executed_amount,
                "remaining_amount": remaining_estimated,
            },
        )
        return

    started_at = _parse_iso_dt(trade.get("partial_fill_started_at")) or _parse_iso_dt(
        trade.get("open_time")
    )
    if started_at is None:
        started_at = datetime.now()
    age_seconds = max(0.0, (datetime.now() - started_at).total_seconds())

    trade["remaining_amount"] = remaining_estimated
    trade["status"] = "PARTIAL_FILL_PENDING"
    trade["partial_fill_pending"] = True

    timeout_s = int(getattr(Config, "PARTIAL_FILL_TIMEOUT_SECONDS", 300) or 300)
    if age_seconds < timeout_s:
        with bot.db_lock:
            bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
        return

    cancel_order = getattr(bot.execution, "cancel_order", None)
    order_id = str(open_entry_order.get("id") or trade.get("entry_exchange_order_id") or "")
    if callable(cancel_order) and order_id:
        try:
            cancel_order(symbol, order_id)
            trade["remaining_amount"] = 0.0
            trade["unfilled_canceled_amount"] = remaining_estimated
            trade["status"] = "OPEN"
            trade["partial_fill_pending"] = False
            with bot.db_lock:
                bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
            append_execution_event(
                bot,
                "PARTIAL_FILL_TIMEOUT_CANCEL",
                {
                    "symbol": symbol,
                    "entry_order_id": order_id,
                    "executed_amount": executed_amount,
                    "canceled_amount": remaining_estimated,
                    "age_seconds": round(age_seconds, 3),
                },
            )
            bot.log(
                f"⏱️ PARTIAL_FILL timeout {symbol}: cancelado remanente {remaining_estimated:.6f}"
            )
        except Exception as error:
            trade["status"] = "PARTIAL_FILL_CANCEL_FAILED"
            trade["partial_fill_pending"] = True
            trade["remaining_amount"] = remaining_estimated
            with bot.lock:
                bot.is_paused = True
                bot.integrity_lock_active = True
                setattr(bot, "halt_system_active", True)
                with bot.db_lock:
                    bot.brain.save_active_trade_state(str(trade.get("trade_key") or symbol), trade)
            append_execution_event(
                bot,
                "PARTIAL_FILL_CANCEL_FAILED",
                {
                    "symbol": symbol,
                    "entry_order_id": order_id,
                    "age_seconds": round(age_seconds, 3),
                    "error": str(error)[:180],
                },
            )
            bot.log(f"⚠️ No se pudo cancelar remanente parcial en {symbol}: {error}")
            send_telegram_msg(
                f"🛑 *PARTIAL_FILL_CANCEL_FAILED* {symbol}\n"
                f"No se pudo cancelar remanente {remaining_estimated:.6f}. "
                "HALT activado para reconciliación manual."
            )


def sync_wallet(bot):
    if bool(getattr(Config, "PAPER_MODE", True)):
        return
    try:
        # Usamos fetch_positions para obtener datos precisos y unificados
        # [FIX] Race Condition: Snapshot de active_trades antes de la llamada de red
        with bot.lock:
            active_trades_snapshot = bot.active_trades.copy()

        positions = bot.execution.fetch_positions()
        real_active_on_binance = {}

        # PROTECCIÓN DE INTEGRIDAD: Si Binance devuelve lista vacía pero tenemos trades REALES activos,
        # podría ser un error de API. Verificamos balance para confirmar que no es un error de conexión.
        if not positions and any(
            not trade.get("is_shadow") for trade in active_trades_snapshot.values()
        ):
            if not Config.PAPER_MODE:
                _halt_wallet_sync(
                    bot,
                    "EMPTY_POSITIONS_WITH_LOCAL_REAL_TRADES",
                    {"local_symbols": list(active_trades_snapshot.keys())},
                )
                return
            if bot.get_current_balance() == 0:
                return  # Si balance es 0 y pos es 0, ok. Si no, sospechoso.

        raw_positions = []
        sides_by_symbol = {}
        for pos in positions:
            amount = float(pos.get("contracts") or 0)
            if abs(amount) > 0:
                # Determinación robusta del lado (Long/Short)
                side = "BUY"
                if pos.get("side") == "short":
                    side = "SELL"
                elif pos.get("side") == "long":
                    side = "BUY"
                else:
                    # Fallback a raw info si ccxt no normalizó el side
                    raw_amt = float(pos["info"].get("positionAmt", 0))
                    side = "BUY" if raw_amt > 0 else "SELL"

                # Normalización robusta para evitar purgas erróneas
                symbol = normalize_position_symbol(pos.get("symbol", ""))

                side = normalize_trade_side(side)
                raw_positions.append(
                    {
                        "symbol": symbol,
                        "side": side,
                        "amount": abs(amount),
                        "entry": float(pos.get("entryPrice") or 0),
                        "pnl": float(pos.get("unrealizedPnl") or 0),
                    }
                )
                sides_by_symbol.setdefault(symbol, set()).add(side)

        for info in raw_positions:
            symbol = info["symbol"]
            side = info["side"]
            side_key = make_trade_key(symbol, side, force_side=True)
            local_key = find_trade_key(bot.active_trades, symbol, side)
            force_side_key = (
                len(sides_by_symbol.get(symbol, set())) > 1 or side_key in bot.active_trades
            )
            trade_key = local_key or make_trade_key(symbol, side, force_side=force_side_key)
            real_active_on_binance[trade_key] = {
                "symbol": symbol,
                "trade_key": trade_key,
                "amount": info["amount"],
                "side": side,
                "entry": info["entry"],
                "pnl": info["pnl"],
            }

        # LOG DE DIAGNÓSTICO: Ver qué detecta Binance
        if real_active_on_binance:
            bot.log(f"🔍 Wallet Sync: Binance reporta {list(real_active_on_binance.keys())}")

        current_balance = bot.get_current_balance()
        balance_lock = getattr(bot, "balance_lock", None)
        if balance_lock:
            with balance_lock:
                bot.balance = current_balance
        else:
            bot.balance = current_balance

        with bot.lock:
            emergency_closed_symbols = set()

            # A. ACTUALIZACIÓN DE PRECIOS REALES (Corrige el PnL)
            for trade_key, info in real_active_on_binance.items():
                symbol = info["symbol"]
                if trade_key in bot.active_trades and not bot.active_trades[trade_key].get(
                    "is_shadow"
                ):
                    # Sincronizamos el precio de entrada del bot con el de Binance
                    # Validamos que el precio sea > 0 para evitar errores de API
                    if info["entry"] > 0 and bot.active_trades[trade_key]["entry"] != info["entry"]:
                        bot.log(
                            f"⚖️ Sincronizando precio {symbol}: {bot.active_trades[trade_key]['entry']} -> {info['entry']}"
                        )
                        bot.active_trades[trade_key]["entry"] = info["entry"]
                        bot.active_trades[trade_key]["amount"] = info["amount"]
                        bot.active_trades[trade_key]["size_usd"] = info["entry"] * info["amount"]

                    emergency_closed = _ensure_hard_sl_attached(
                        bot, symbol, bot.active_trades[trade_key], info
                    )
                    _manage_partial_fill_trade(bot, symbol, bot.active_trades[trade_key], info)
                    if emergency_closed:
                        emergency_closed_symbols.add(trade_key)

            # B. PURGAR trades huerfanos (No están en Binance pero sí en el bot)
            for trade_key in list(bot.active_trades.keys()):
                trade = bot.active_trades[trade_key]
                symbol = str(trade.get("symbol") or trade_key).split("|")[0]
                if (
                    not trade.get("is_shadow")
                    and trade_key not in real_active_on_binance
                    and not Config.PAPER_MODE
                ):
                    # PROTECCIÓN DE LATENCIA: No purgar si el trade tiene menos de 120 segundos
                    open_time = parse_datetime_utc(trade.get("open_time") or utc_now())
                    if (utc_now() - open_time).total_seconds() < 120:
                        continue

                    # No purgar si el estado es ambiguo (orden no confirmada, cierre en curso, etc.)
                    ambiguous_statuses = {
                        "PENDING_SEND",
                        "ENTRY_ACK_UNKNOWN",
                        "EXIT_STUCK",
                        "CLOSING_INITIATED",
                        "PARTIAL_FILL_PENDING",
                    }
                    status = trade.get("status", "")
                    if status in ambiguous_statuses:
                        bot.log(
                            f"⏳ Wallet sync: {symbol} tiene estado ambiguo ({status}), no purgando"
                        )
                        continue

                    if not _exchange_position_is_flat(bot, symbol):
                        _halt_wallet_sync(
                            bot,
                            "LOCAL_TRADE_MISSING_FROM_SNAPSHOT_NOT_FLAT",
                            {"symbol": symbol, "trade_key": trade_key},
                        )
                        continue

                    existing_sl = _find_existing_hard_sl_order(bot, symbol, trade)
                    if existing_sl is _HARD_SL_ORDER_LOOKUP_FAILED:
                        _halt_wallet_sync(
                            bot,
                            "ORPHAN_PURGE_OPEN_ORDERS_LOOKUP_FAILED",
                            {"symbol": symbol, "trade_key": trade_key},
                        )
                        continue
                    if existing_sl:
                        _halt_wallet_sync(
                            bot,
                            "LOCAL_TRADE_MISSING_BUT_PROTECTION_EXISTS",
                            {
                                "symbol": symbol,
                                "trade_key": trade_key,
                                "sl_exchange_order_id": existing_sl.get("id"),
                            },
                        )
                        continue

                    bot.log(f"🧹 Purgando manual: {symbol}")
                    del bot.active_trades[trade_key]
                    with bot.db_lock:
                        bot.brain.delete_active_trade_state(trade_key)

            # C. ADOPTAR trades nuevos (Si abres algo manual en Binance)
            for trade_key, info in real_active_on_binance.items():
                symbol = info["symbol"]
                if trade_key in emergency_closed_symbols:
                    continue
                if trade_key not in bot.active_trades:
                    bot.log(
                        f"📥 CARTERA: Detectado nuevo trade en Binance: {symbol}. Sincronizando..."
                    )
                    base = symbol.split("/")[0]
                    sector = next(
                        (
                            key
                            for key, values in Config.SECTORS.items()
                            if any(item.lower() in base.lower() for item in values)
                        ),
                        "OTHE",
                    )
                    sl = info["entry"] * 0.95 if info["side"] == "BUY" else info["entry"] * 1.05

                    # Generate stable client order IDs for this adopted position
                    entry_coid, sl_coid, _tp_coid = generate_order_ids(
                        symbol, info["side"], utc_now().timestamp(), "wallet-sync"
                    )

                    bot.active_trades[trade_key] = {
                        "trade_key": trade_key,
                        "symbol": symbol,
                        "side": info["side"],
                        "entry": info["entry"],
                        "amount": info["amount"],
                        "size_usd": info["entry"] * info["amount"],
                        "open_time": datetime.now(),
                        "pnl": 0.0,
                        "is_shadow": False,
                        "simulated_real": False,
                        "sector": sector,
                        "sl": sl,
                        "tp": 0.0,
                        "trailing_active": False,
                        "early_be_armed": False,
                        "mae_price": info["entry"],
                        "mfe_price": info["entry"],
                        "market_snapshot": {
                            "prob_final": 99.0,
                            "votos": {"G": 99.0},
                            "is_adopted": True,
                        },
                        "status": "OPEN",
                        "entry_client_order_id": entry_coid,
                        "sl_client_order_id": sl_coid,
                        "sl_exchange_order_id": None,
                    }
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(trade_key, bot.active_trades[trade_key])
                    _ensure_hard_sl_attached(bot, symbol, bot.active_trades[trade_key], info)
    except Exception as error:
        bot.log(f"⚠️ Error Sync: {error}")
        if not Config.PAPER_MODE:
            _halt_wallet_sync(
                bot,
                "WALLET_SYNC_EXCEPTION",
                {"error": str(error)[:220]},
            )

import hashlib
import logging
import traceback

from config import Config

logger = logging.getLogger(__name__)
from core.config.operational import OperationalConfig
from core.execution_telemetry import append_execution_event
from core.symbol_utils import normalize_position_symbol
from core.time_utils import parse_datetime_utc, utc_now, utc_now_iso
from core.trade_keys import make_trade_key, normalize_trade_side
from core.trade_state import TradeStatus
from tools.notifier import send_telegram_msg

PENDING_SEND_STALE_SECONDS = 30

# Prefixes cortos para cada tipo de orden (2 caracteres cada uno)
_ENTRY_PFIX = "E_"
_SL_PFIX = "S_"
_TP_PFIX = "T_"
_MAX_BINANCE_ID_LEN = 36
_MAX_SAFE_ID_LEN = 32  # Margen de 4 caracteres para emergencias


def _make_hash(seed: str, length: int) -> str:
    """Genera un hex digest truncado de longitud fija."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def generate_order_ids(
    symbol: str, side: str, signal_ts: float, instance_id: str
) -> tuple[str, str, str]:
    """Genera IDs para entry, SL y TP desde una semilla común.

    Formato: {prefijo}{hash_truncado}
    - Entry: E_{20 chars} = 22 caracteres
    - SL: S_{20 chars} = 22 caracteres
    - TP: T_{20 chars} = 22 caracteres

    Todos son deterministas, trazables y <= _MAX_SAFE_ID_LEN.
    """
    base_seed = f"{signal_ts:.6f}|{symbol}|{side}|{instance_id}"
    hash_len = 20  # 2 (prefijo) + 1 (separador implícito en prefijo) + 20 = 23 máximo

    entry_hash = _make_hash(f"{base_seed}|entry", hash_len)
    sl_hash = _make_hash(f"{base_seed}|sl", hash_len)
    tp_hash = _make_hash(f"{base_seed}|tp", hash_len)

    entry_id = f"{_ENTRY_PFIX}{entry_hash}"
    sl_id = f"{_SL_PFIX}{sl_hash}"
    tp_id = f"{_TP_PFIX}{tp_hash}"

    # Validación estructural
    for name, val in [("entry", entry_id), ("sl", sl_id), ("tp", tp_id)]:
        if len(val) > _MAX_SAFE_ID_LEN:
            raise ValueError(
                f"CRITICAL: {name} ID '{val}' exceeds {_MAX_SAFE_ID_LEN} chars limit. "
                f"Length: {len(val)}"
            )
        if len(val) > _MAX_BINANCE_ID_LEN:
            raise ValueError(
                f"CRITICAL: {name} ID '{val}' exceeds Binance 36 chars limit. Length: {len(val)}"
            )

    return entry_id, sl_id, tp_id


def validate_binance_limits(order_id: str) -> None:
    """Valida que el ID cumpla con los límites de Binance.

    Raises:
        ValueError: Si el ID excede 36 caracteres.
    """
    if len(order_id) > _MAX_BINANCE_ID_LEN:
        raise ValueError(
            f"CRITICAL: ClientOrderId '{order_id}' exceeds "
            f"{_MAX_BINANCE_ID_LEN} chars limit. Length: {len(order_id)}"
        )


# --- Funciones legacy ---
# Mantenidas para compatibilidad con código existente.


def generate_client_order_id(symbol: str, side: str, signal_ts: float, instance_id: str) -> str:
    """DEPRECADO: Usar generate_order_ids().

    Genera un client_order_id para entrada (formato legacy).
    """
    entry_id, _, _ = generate_order_ids(symbol, side, signal_ts, instance_id)
    return entry_id


def _extract_client_order_id(order: dict) -> str:
    if not isinstance(order, dict):
        return ""
    direct = order.get("clientOrderId")
    if direct:
        return str(direct)
    info = order.get("info") or {}
    if not isinstance(info, dict):
        return ""
    return str(info.get("clientOrderId") or info.get("origClientOrderId") or "")


def _build_open_order_index(open_orders):
    by_client_order_id = {}
    by_symbol = {}
    for order in open_orders or []:
        if not isinstance(order, dict):
            continue
        symbol = normalize_position_symbol(order.get("symbol", ""))
        if symbol:
            by_symbol.setdefault(symbol, []).append(order)
        coid = _extract_client_order_id(order)
        if coid:
            by_client_order_id[coid] = order
    return by_client_order_id, by_symbol


def _bool_reduce_only_order(order: dict) -> bool:
    info = order.get("info") or {}
    raw = order.get("reduceOnly", info.get("reduceOnly", False))
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"true", "1", "yes"}


def _bool_close_position(order: dict) -> bool:
    info = order.get("info") or {}
    raw = order.get("closePosition", info.get("closePosition", False))
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"true", "1", "yes"}


def _find_existing_orphan_stop(open_orders_by_symbol: dict, symbol: str, side: str):
    expected_side = "SELL" if str(side).upper() == "BUY" else "BUY"
    for order in open_orders_by_symbol.get(symbol, []) or []:
        order_type = str(order.get("type") or (order.get("info") or {}).get("type") or "").upper()
        if "STOP" not in order_type:
            continue
        if not _bool_reduce_only_order(order):
            continue
        if str(order.get("side") or "").upper() == expected_side:
            return order
    return None


def _normalize_order_status(raw_status: str) -> str:
    status = str(raw_status or "").upper()
    if status in {"NEW", "OPEN", "PARTIALLY_FILLED"}:
        return "OPEN"
    if status in {"FILLED", "CLOSED"}:
        return "FILLED"
    if status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
        return "CANCELED"
    return status or "UNKNOWN"


def generate_child_client_order_id(entry_client_order_id: str, leg: str) -> str:
    """Genera ID corto y determinista para legs (SL/TP) de recovery.

    Formato: {leg_prefix}_{hash}
    - Máximo _MAX_SAFE_ID_LEN (32) caracteres
    - Valida contra _MAX_BINANCE_ID_LEN (36)
    """
    leg_safe = str(leg or "LEG").upper()[:6]
    # Hash de la semilla; no concatenar entry_client_order_id completo para evitar exceder limite
    raw = f"{entry_client_order_id}|{leg_safe}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    result = f"{leg_safe}_{digest}"[:_MAX_SAFE_ID_LEN]

    # Validacion estructural
    if len(result) > _MAX_SAFE_ID_LEN:
        raise ValueError(
            f"CRITICAL: child ID '{result}' exceeds {_MAX_SAFE_ID_LEN} chars limit. "
            f"Length: {len(result)}"
        )
    if len(result) > _MAX_BINANCE_ID_LEN:
        raise ValueError(
            f"CRITICAL: child ID '{result}' exceeds Binance 36 chars limit. Length: {len(result)}"
        )
    return result


def _validate_orphan_size(entry: float, amount: float) -> tuple:
    """Valida el tamaño de una posición huérfana.
    Returns: (is_valid: bool, reason: str, size_usd: float)
    """
    min_size = float(getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0))
    max_size = float(getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0))
    size_usd = entry * amount

    if size_usd < min_size:
        return False, f"tamaño ${size_usd:.2f} < mínimo ${min_size:.2f}", size_usd
    if size_usd > max_size:
        return False, f"tamaño ${size_usd:.2f} > máximo ${max_size:.2f}", size_usd
    return True, "", size_usd


def _compute_orphan_sl(entry: float, side: str, market_price: float) -> float:
    """Calcula SL dinámico para posición huérfana."""
    if market_price and market_price > 0:
        atr_multiplier = float(getattr(OperationalConfig, "ORPHAN_SL_ATR_MULTIPLIER", 2.0))
        if side == "BUY":
            return entry - (market_price * atr_multiplier / 100.0)
        else:
            return entry + (market_price * atr_multiplier / 100.0)

    percentage = float(getattr(OperationalConfig, "ORPHAN_SL_PERCENTAGE", 0.005))
    if side == "BUY":
        return entry * (1 - percentage)
    else:
        return entry * (1 + percentage)


def _verify_orphan_multiple(bot, symbol: str) -> tuple:
    """Verifica huérfano desde múltiples endpoints.
    Returns: (is_valid: bool, reason: str)
    """
    fetch_position = getattr(bot.execution, "fetch_position", None)
    if not callable(fetch_position):
        return True, ""

    try:
        pos = fetch_position(symbol)
        if pos and isinstance(pos, dict):
            amount = float(pos.get("contracts") or 0)
            if abs(amount) > 0:
                return True, ""
            return False, f"fetch_position({symbol}) returned amount=0"
    except Exception as e:
        return False, f"fetch_position failed: {e}"

    return True, ""


def _halt_unadoptable_real_orphan(bot, symbol: str, reason: str, details: dict | None = None):
    bot.is_paused = True
    bot.integrity_lock_active = True
    setattr(bot, "halt_system_active", True)
    payload = dict(details or {})
    payload["reason"] = reason
    with bot.db_lock:
        bot.brain.save_error_snapshot(symbol, "REAL_ORPHAN_UNADOPTABLE_HALT", payload)
    append_execution_event(
        bot,
        "REAL_ORPHAN_UNADOPTABLE_HALT",
        {"symbol": symbol, "reason": reason, **payload},
    )
    bot.log(f"🛑 REAL_ORPHAN_UNADOPTABLE {symbol}: {reason}")


def reconcile_bootstrap_state(bot):
    """Sincroniza estado DB <-> Exchange al arrancar para evitar huérfanos/ghosts."""
    try:
        with bot.lock:
            db_snapshot = dict(bot.active_trades)

        try:
            positions = bot.execution.fetch_positions() or []
        except Exception as error:
            bot.log(
                f"⚠️ Reconciliación abortada: no se pudieron consultar posiciones del exchange: {error}"
            )
            if not Config.PAPER_MODE:
                bot.is_paused = True
                bot.integrity_lock_active = True
                setattr(bot, "halt_system_active", True)
                with bot.db_lock:
                    bot.brain.save_error_snapshot(
                        "SYSTEM",
                        "BOOTSTRAP_RECONCILIATION_FAILED",
                        {"error": str(error)[:220], "source": "fetch_positions"},
                    )
                append_execution_event(
                    bot,
                    "BOOTSTRAP_RECONCILIATION_FAILED_HALT",
                    {"error": str(error)[:180]},
                )
            return
        open_orders = []
        fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
        if callable(fetch_open_orders):
            try:
                open_orders = fetch_open_orders() or []
            except Exception as error:
                bot.log(f"⚠️ No se pudieron consultar open orders en reconciliación: {error}")
                if not Config.PAPER_MODE:
                    bot.is_paused = True
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    with bot.db_lock:
                        bot.brain.save_error_snapshot(
                            "SYSTEM",
                            "BOOTSTRAP_RECONCILIATION_FAILED",
                            {"error": str(error)[:220], "source": "fetch_open_orders"},
                        )
                    append_execution_event(
                        bot,
                        "BOOTSTRAP_RECONCILIATION_FAILED_HALT",
                        {"error": str(error)[:180], "source": "fetch_open_orders"},
                    )
                    return
        raw_positions = []
        sides_by_symbol = {}
        for pos in positions:
            amount = float(pos.get("contracts") or 0)
            if abs(amount) <= 0:
                continue
            symbol = normalize_position_symbol(pos.get("symbol", ""))
            if not symbol:
                continue
            side = "BUY"
            if pos.get("side") == "short":
                side = "SELL"
            elif pos.get("side") not in ("long", "short"):
                raw_amt = float(pos.get("info", {}).get("positionAmt", 0) or 0)
                side = "BUY" if raw_amt > 0 else "SELL"
            side = normalize_trade_side(side)
            raw_positions.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry": float(pos.get("entryPrice") or 0),
                    "amount": abs(amount),
                }
            )
            sides_by_symbol.setdefault(symbol, set()).add(side)

        db_snapshot = {}
        for key, state in dict(bot.active_trades).items():
            if not isinstance(state, dict):
                db_snapshot[key] = state
                continue
            state.setdefault("trade_key", str(key))
            db_snapshot[str(key)] = state

        exchange_positions = {}
        for pos in raw_positions:
            symbol = pos["symbol"]
            side = pos["side"]
            side_key = make_trade_key(symbol, side, force_side=True)
            force_side_key = len(sides_by_symbol.get(symbol, set())) > 1 or side_key in db_snapshot
            trade_key = make_trade_key(symbol, side, force_side=force_side_key)
            exchange_positions[trade_key] = {
                "symbol": symbol,
                "side": side,
                "entry": pos["entry"],
                "amount": pos["amount"],
                "trade_key": trade_key,
            }

        open_orders_by_coid, open_orders_by_symbol = _build_open_order_index(open_orders)

        db_keys = {s for s, t in db_snapshot.items() if not (t or {}).get("is_shadow", False)}
        position_keys = set(exchange_positions.keys())

        adopted = 0
        lost = 0
        pending_open = 0
        intent_expired = 0

        # Caso 1: posición en Exchange pero no en DB -> adopción forzosa
        missing_in_db = sorted(position_keys - db_keys)
        for trade_key in missing_in_db:
            info = exchange_positions[trade_key]
            symbol = info["symbol"]
            entry_price = info["entry"]
            amount = info["amount"]

            size_valid, size_reason, size_usd = _validate_orphan_size(entry_price, amount)
            if not size_valid:
                bot.log(f"⚠️ Huérfano {symbol} rechazado: {size_reason}")
                if not Config.PAPER_MODE:
                    _halt_unadoptable_real_orphan(
                        bot,
                        symbol,
                        size_reason,
                        {"entry": entry_price, "amount": amount},
                    )
                continue

            verify_valid, verify_reason = _verify_orphan_multiple(bot, symbol)
            if not verify_valid:
                bot.log(
                    f"⚠️ Huérfano {symbol} rechazado: verificación múltiple falló: {verify_reason}"
                )
                if not Config.PAPER_MODE:
                    _halt_unadoptable_real_orphan(
                        bot,
                        symbol,
                        verify_reason,
                        {"entry": entry_price, "amount": amount, "size_usd": size_usd},
                    )
                continue

            market_price = 0.0
            fetch_ticker = getattr(bot.execution, "fetch_ticker", None)
            if callable(fetch_ticker):
                try:
                    ticker = fetch_ticker(symbol)
                    market_price = float(ticker.get("last") or ticker.get("markPrice") or 0.0)
                except Exception as e:
                    bot.log(f"⚠️ No se pudo obtener ticker para {symbol}: {e}")

            sl = _compute_orphan_sl(entry_price, info["side"], market_price)
            adopted_trade = {
                "trade_key": trade_key,
                "symbol": symbol,
                "side": info["side"],
                "entry": entry_price,
                "amount": amount,
                "size_usd": size_usd,
                "open_time": utc_now_iso(),
                "pnl": 0.0,
                "is_shadow": False,
                "simulated_real": False,
                "sector": "OTHE",
                "sl": sl,
                "tp": 0.0,
                "trailing_active": False,
                "early_be_armed": False,
                "mae_price": entry_price,
                "mfe_price": entry_price,
                "market_snapshot": {
                    "is_adopted": True,
                    "prob_final": 99.0,
                    "market_price": market_price,
                },
                "adopted_orphan": True,
            }
            entry_coid, sl_coid, _tp_coid = generate_order_ids(
                symbol,
                info["side"],
                utc_now().timestamp(),
                str(getattr(bot, "instance_uuid", "bootstrap-reconcile")),
            )
            adopted_trade["entry_client_order_id"] = entry_coid
            adopted_trade["sl_client_order_id"] = sl_coid
            existing_stop = _find_existing_orphan_stop(open_orders_by_symbol, symbol, info["side"])
            if existing_stop:
                stop_amount_raw = existing_stop.get("amount") or 0
                try:
                    stop_amount = float(stop_amount_raw)
                except (TypeError, ValueError):
                    stop_amount = 0.0
                stop_close_position = _bool_close_position(existing_stop)
                if stop_close_position or stop_amount >= float(amount):
                    adopted_trade["sl_exchange_order_id"] = existing_stop.get("id")
                    adopted_trade["sl_client_order_id"] = (
                        _extract_client_order_id(existing_stop) or sl_coid
                    )
                    adopted_trade["status"] = "OPEN"
                    with bot.lock:
                        bot.active_trades[trade_key] = adopted_trade
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(trade_key, adopted_trade)
                    adopted += 1
                    continue
                else:
                    logger.warning(
                        "reconciliation orphan stop %s amount %.4f < position %.4f for %s; "
                        "not adopted as HARD SL coverage",
                        existing_stop.get("id"),
                        stop_amount,
                        float(amount),
                        symbol,
                    )
            try:
                sl_order = bot.execution.place_hard_sl(
                    symbol,
                    info["side"],
                    amount,
                    sl,
                    client_order_id=sl_coid,
                )
                if sl_order:
                    adopted_trade["sl_exchange_order_id"] = sl_order.get("id")
                    adopted_trade["status"] = "OPEN"
                    with bot.lock:
                        bot.active_trades[trade_key] = adopted_trade
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(trade_key, adopted_trade)
                else:
                    bot.is_paused = True
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    adopted_trade["status"] = "ADOPTED_UNPROTECTED"
                    with bot.lock:
                        bot.active_trades[trade_key] = adopted_trade
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(trade_key, adopted_trade)
                        bot.brain.save_error_snapshot(
                            symbol,
                            "ORPHAN_HARD_SL_ATTACH_FAILED",
                            {"sl": sl, "amount": amount, "side": info["side"]},
                        )
                    append_execution_event(
                        bot,
                        "ORPHAN_HARD_SL_ATTACH_FAILED_HALT",
                        {"symbol": symbol, "sl": sl, "amount": amount},
                    )
            except Exception as e:
                bot.log(f"⚠️ No se pudo adjuntar SL para huérfana {symbol}: {e}")
                bot.is_paused = True
                bot.integrity_lock_active = True
                setattr(bot, "halt_system_active", True)
                adopted_trade["status"] = "ADOPTED_UNPROTECTED"
                with bot.lock:
                    bot.active_trades[trade_key] = adopted_trade
                with bot.db_lock:
                    bot.brain.save_active_trade_state(trade_key, adopted_trade)
                    bot.brain.save_error_snapshot(
                        symbol,
                        "ORPHAN_HARD_SL_ATTACH_EXCEPTION",
                        {"error": str(e)[:220], "sl": sl, "amount": amount},
                    )

            send_telegram_msg(
                f"🚨 *POSICIÓN HUÉRFANA ADOPTADA*\n"
                f"Símbolo: {symbol}\n"
                f"Lado: {info['side']}\n"
                f"Entry: {entry_price:.6f}\n"
                f"Market: {market_price:.6f}\n"
                f"Size: ${size_usd:.2f}\n"
                f"SL: {sl:.6f}"
            )
            adopted += 1

        # Caso 2: en DB abierta pero no en Exchange -> LOST_IN_TRANSMISSION
        safe_pending_keys = set()
        expired_keys = set()
        for trade_key in sorted(db_keys - position_keys):
            state = db_snapshot.get(trade_key) or {}
            if not isinstance(state, dict):
                continue
            state.setdefault("trade_key", trade_key)
            symbol = normalize_position_symbol(state.get("symbol", trade_key))
            entry_coid = str(state.get("entry_client_order_id") or "")
            if not entry_coid:
                continue

            status = str(state.get("status") or "").upper()

            intent_created = state.get("intent_created_at_utc") or state.get("open_time")
            intent_age_seconds = None
            if intent_created:
                try:
                    intent_age_seconds = max(
                        0.0,
                        (utc_now() - parse_datetime_utc(intent_created)).total_seconds(),
                    )
                except Exception:
                    import traceback as _tb

                    _tb.print_exc()
                    intent_age_seconds = None

            exchange_order = None
            order_lookup_failed = False
            order_lookup_error = ""
            fetch_by_coid = getattr(bot.execution, "fetch_order_by_client_id", None)
            if callable(fetch_by_coid):
                try:
                    exchange_order = fetch_by_coid(symbol, entry_coid)
                except Exception as error:
                    order_lookup_failed = True
                    order_lookup_error = str(error)[:220]
                    bot.log(f"⚠️ Consulta order-by-client-id falló {symbol}/{entry_coid}: {error}")

            if exchange_order is None and entry_coid in open_orders_by_coid:
                exchange_order = open_orders_by_coid[entry_coid]

            state["intent_last_check_at_utc"] = utc_now_iso()
            state["intent_check_attempts"] = int(state.get("intent_check_attempts", 0) or 0) + 1

            if order_lookup_failed and exchange_order is None:
                state["status"] = "ORDER_LOOKUP_FAILED"
                state["order_lookup_error"] = order_lookup_error
                state["reconciled_at"] = utc_now_iso()
                safe_pending_keys.add(trade_key)
                with bot.lock:
                    bot.active_trades[trade_key] = state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(trade_key, state)
                    bot.brain.save_error_snapshot(
                        symbol,
                        "ORDER_LOOKUP_FAILED",
                        {
                            "entry_client_order_id": entry_coid,
                            "error": order_lookup_error,
                            "reconciliation_ts": state["reconciled_at"],
                        },
                    )
                append_execution_event(
                    bot,
                    "ORDER_LOOKUP_FAILED_KEEP_INTENT",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_coid,
                        "error": order_lookup_error,
                    },
                )
                if not Config.PAPER_MODE:
                    with bot.lock:
                        bot.integrity_lock_active = True
                        setattr(bot, "halt_system_active", True)
                    bot.log(
                        f"🛑 ORDER_LOOKUP_FAILED en {symbol} activa HALT para modo REAL. "
                        f"Requiere intervención manual."
                    )
                    send_telegram_msg(
                        f"🛑 *ORDER_LOOKUP_FAILED* {symbol} activó HALT en modo REAL. "
                        f"Error: {order_lookup_error[:100]}. Requiere intervención manual."
                    )
                continue

            if exchange_order is None:
                if status in {TradeStatus.PENDING_SEND.value, TradeStatus.ENTRY_ACK_UNKNOWN.value}:
                    stale_limit = float(
                        getattr(
                            bot,
                            "pending_send_stale_seconds",
                            PENDING_SEND_STALE_SECONDS,
                        )
                    )
                    age = intent_age_seconds if intent_age_seconds is not None else 0.0

                    if age < stale_limit:
                        state.setdefault("intent_created_at_utc", utc_now_iso())
                        safe_pending_keys.add(trade_key)
                        with bot.lock:
                            bot.active_trades[trade_key] = state
                        with bot.db_lock:
                            bot.brain.save_active_trade_state(trade_key, state)
                        continue

                    with bot.db_lock:
                        bot.brain.save_error_snapshot(
                            symbol,
                            "INTENT_EXPIRED",
                            {
                                "entry_client_order_id": entry_coid,
                                "age_seconds": round(float(age), 3),
                                "stale_limit_seconds": stale_limit,
                                "reconciliation_ts": utc_now_iso(),
                            },
                        )
                        bot.brain.delete_active_trade_state(trade_key)
                    append_execution_event(
                        bot,
                        "INTENT_EXPIRED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_coid,
                            "age_seconds": round(float(age), 3),
                            "stale_limit_seconds": stale_limit,
                        },
                    )
                    with bot.lock:
                        bot.active_trades.pop(trade_key, None)
                    send_telegram_msg(
                        f"⚠️ *INTENT_EXPIRED* {symbol}\n"
                        f"PENDING_SEND sin orden exchange tras {age:.1f}s. "
                        "Intención descartada por reconciliación."
                    )
                    intent_expired += 1
                    expired_keys.add(trade_key)
                continue
            if not isinstance(exchange_order, dict):
                continue

            status_raw = str(exchange_order.get("status") or "")
            normalized_status = _normalize_order_status(status_raw)
            if normalized_status == "OPEN":
                state["status"] = TradeStatus.PENDING_EXCHANGE_OPEN.value
                state["exchange_open_order_id"] = exchange_order.get("id")
                state["exchange_open_order_status"] = exchange_order.get("status")
                state["reconciled_at"] = utc_now_iso()
                state["intent_created_at_utc"] = state.get("intent_created_at_utc") or utc_now_iso()
                with bot.lock:
                    bot.active_trades[trade_key] = state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(trade_key, state)
                safe_pending_keys.add(trade_key)
                pending_open += 1
            elif normalized_status == "FILLED":
                state["status"] = TradeStatus.ENTRY_FILLED_AWAITING_POSITION_SYNC.value
                state["exchange_entry_order_id"] = exchange_order.get("id")
                state["exchange_open_order_status"] = exchange_order.get("status")
                state["reconciled_at"] = utc_now_iso()
                state["intent_created_at_utc"] = state.get("intent_created_at_utc") or utc_now_iso()
                with bot.lock:
                    bot.active_trades[trade_key] = state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(trade_key, state)
                safe_pending_keys.add(trade_key)

        missing_in_exchange = sorted(((db_keys - position_keys) - safe_pending_keys) - expired_keys)
        for trade_key in missing_in_exchange:
            state = db_snapshot.get(trade_key) or {}
            symbol = normalize_position_symbol((state or {}).get("symbol", trade_key))
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "LOST_IN_TRANSMISSION",
                    {"reconciliation_ts": utc_now_iso()},
                )
                bot.brain.delete_active_trade_state(trade_key)
            with bot.lock:
                if trade_key in bot.active_trades:
                    del bot.active_trades[trade_key]
            lost += 1

        # En PAPER_MODE el balance es virtual; no se compara contra custodia real.
        if Config.PAPER_MODE:
            if not float(getattr(bot, "balance", 0.0) or 0.0):
                with bot.balance_lock:
                    bot.balance = Config.PAPER_INITIAL_BALANCE
            if not float(getattr(bot, "available_balance", 0.0) or 0.0):
                bot.available_balance = Config.PAPER_INITIAL_BALANCE
            if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                bot.daily_initial_balance = Config.PAPER_INITIAL_BALANCE
            if not bool(getattr(bot, "halt_system_active", False)):
                bot.integrity_lock_active = False
            return

        # Integrity lock por discrepancia de balance
        try:
            exchange_balance = float(bot.get_current_balance() or 0.0)
        except Exception as error:
            bot.log(
                f"⚠️ Reconciliación: no se pudo obtener balance del exchange para integrity lock: {error}"
            )
            exchange_balance = 0.0
        local_balance = float(getattr(bot, "balance", 0.0) or 0.0)
        diff_pct = 0.0
        if exchange_balance > 0:
            diff_pct = abs(local_balance - exchange_balance) / exchange_balance * 100.0

        if diff_pct > 0.1:
            bot.integrity_lock_active = True
            bot.is_paused = True
            send_telegram_msg(
                f"🛑 *INTEGRITY LOCK*\nDiscrepancia balance {diff_pct:.3f}% (>0.1%).\n"
                f"Local: ${local_balance:.2f} | Exchange: ${exchange_balance:.2f}\n"
                f"Use /rebase_capital para reanclar capital."
            )
            bot.log(
                f"🛑 INTEGRITY_LOCK activado: diff={diff_pct:.3f}% local={local_balance:.2f} ex={exchange_balance:.2f}"
            )

        if adopted or lost or pending_open or intent_expired:
            bot.log(
                "🔁 Reconciliación bootstrap: "
                f"adoptadas={adopted} | pending_open={pending_open} | intent_expired={intent_expired} | lost_in_tx={lost}"
            )

    except Exception as e:
        bot.log(f"⚠️ Error en reconciliación de arranque: {e}")
        bot.log(traceback.format_exc())
        if not Config.PAPER_MODE:
            raise


def allocate_signal_timestamp() -> float:
    return utc_now().timestamp()


def recover_halt_if_exchange_consistent(bot, required_snapshots: int = 2) -> tuple[bool, str]:
    """Release HALT only after repeated flat exchange snapshots.

    This is intentionally conservative: it only auto-recovers when there are no
    local real trades, no exchange positions, and balance is readable/positive.
    """
    required = max(1, int(required_snapshots or 1))
    with bot.lock:
        local_real_symbols = sorted(
            symbol
            for symbol, trade in (getattr(bot, "active_trades", {}) or {}).items()
            if isinstance(trade, dict) and not trade.get("is_shadow", False)
        )
    if local_real_symbols:
        return False, f"RECOVERY_BLOCKED_LOCAL_REAL: {', '.join(local_real_symbols)}"

    try:
        positions = bot.execution.fetch_positions() or []
    except Exception as error:
        return False, f"RECOVERY_BLOCKED_POSITIONS_UNREADABLE: {error}"

    exchange_symbols = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        amount = pos.get("contracts")
        if amount is None:
            amount = (
                (pos.get("info") or {}).get("positionAmt", 0)
                if isinstance(pos.get("info"), dict)
                else 0
            )
        if abs(float(amount or 0.0)) > 0.0:
            exchange_symbols.append(normalize_position_symbol(pos.get("symbol", "")))
    exchange_symbols = sorted(symbol for symbol in exchange_symbols if symbol)
    if exchange_symbols:
        return False, f"RECOVERY_BLOCKED_EXCHANGE_EXPOSURE: {', '.join(exchange_symbols)}"

    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    open_orders: list[dict] = []
    if callable(fetch_open_orders):
        try:
            open_orders = fetch_open_orders() or []
        except Exception as error:
            return False, f"RECOVERY_BLOCKED_OPEN_ORDERS_UNREADABLE: {error}"
    if open_orders:
        open_symbols = sorted(set(o.get("symbol", "") for o in open_orders if isinstance(o, dict)))
        return False, f"RECOVERY_BLOCKED_OPEN_ORDERS: {', '.join(open_symbols)}"

    try:
        exchange_balance = float(bot.get_current_balance() or 0.0)
    except Exception as error:
        return False, f"RECOVERY_BLOCKED_BALANCE_UNREADABLE: {error}"
    if exchange_balance <= 0:
        return False, "RECOVERY_BLOCKED_BALANCE_NON_POSITIVE"

    fingerprint = {"exchange_flat": True, "balance": round(exchange_balance, 8)}
    state = getattr(bot, "_halt_recovery_state", {}) or {}
    attempts = int(state.get("attempts", 0) or 0) + 1
    max_attempts = int(Config.HALT_RECOVERY_MAX_ATTEMPTS or 5)
    if attempts > max_attempts:
        bot._halt_recovery_state = {
            "fingerprint": fingerprint,
            "count": int(state.get("count", 0) or 0),
            "attempts": attempts,
        }
        append_execution_event(
            bot,
            "HALT_RECOVERY_MAX_ATTEMPTS",
            {"attempts": attempts, "max_attempts": max_attempts},
        )
        return False, f"RECOVERY_BLOCKED_MAX_ATTEMPTS: {attempts}/{max_attempts}"

    if state.get("fingerprint") == fingerprint:
        count = int(state.get("count", 0) or 0) + 1
    else:
        count = 1
    bot._halt_recovery_state = {"fingerprint": fingerprint, "count": count, "attempts": attempts}

    if count < required:
        return False, f"RECOVERY_PENDING_CONSISTENT_SNAPSHOTS: {count}/{required}"

    balance_lock = getattr(bot, "balance_lock", None)
    if balance_lock:
        with balance_lock:
            bot.balance = exchange_balance
            bot.daily_initial_balance = exchange_balance
    else:
        bot.balance = exchange_balance
        bot.daily_initial_balance = exchange_balance

    with bot.lock:
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.is_paused = False
        bot._halt_recovery_state = {"fingerprint": fingerprint, "count": count, "attempts": 0}
    append_execution_event(
        bot,
        "HALT_RECOVERY_RELEASED",
        {"balance": exchange_balance, "snapshots": count},
    )
    return True, f"RECOVERY_OK: exchange flat, balance=${exchange_balance:.2f}, snapshots={count}"

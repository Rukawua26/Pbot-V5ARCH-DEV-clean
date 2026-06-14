from __future__ import annotations

import importlib.util
import time
from contextlib import nullcontext
from typing import Any

from config import Config
from core.execution_telemetry import append_execution_event
from core.risk_policy import evaluate_entry_risk_decision, record_risk_decision
from core.symbol_utils import normalize_position_symbol
from core.trade_state import open_trade_statuses
from tools.learning import shadow_logger
from tools.notifier import send_telegram_msg


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _clamp_leverage_1_to_10(raw_leverage) -> int:
    try:
        lev = int(float(raw_leverage))
    except (TypeError, ValueError):
        lev = 10
    return max(1, min(lev, 10))


def _emergency_close_verify_flat(bot, symbol: str) -> bool:
    fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
    if not callable(fetch_positions):
        return False
    positions = fetch_positions() or []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if normalize_position_symbol(pos.get("symbol", "")) != symbol:
            continue
        contracts = pos.get("contracts")
        if contracts is None:
            contracts = (pos.get("info") or {}).get("positionAmt", 0)
        if abs(float(contracts or 0.0)) > 0.0:
            return False
    return True


def _emergency_market_close(
    bot,
    symbol: str,
    side: str,
    amount: float,
    verify_flat: bool,
    persist_state: bool,
    halt_on_failure: bool,
    trade: dict | None = None,
    sl_error: str | None = None,
    append_event_fn=None,
    send_telegram_fn=None,
) -> tuple[bool, dict]:
    started = time.perf_counter()
    close_ok = False
    last_close_error = ""
    result = {
        "ttr_seconds": 0.0,
        "last_error": "",
        "close_ok": False,
    }

    exit_side = "SELL" if str(side).upper() == "BUY" else "BUY"

    def _close_result_confirmed(order_result) -> bool:
        if verify_flat:
            return _emergency_close_verify_flat(bot, symbol)
        if not isinstance(order_result, dict):
            return bool(order_result)
        exit_state = str(order_result.get("exit_state") or "").upper()
        status = str(order_result.get("status") or "").lower()
        if exit_state == "FILLED" or status in {"closed", "filled"}:
            return True
        return False

    if persist_state and trade:
        trade["status"] = "CLOSING_INITIATED"
        trade["closing_in_progress"] = True
        with bot.db_lock:
            bot.brain.save_active_trade_state(symbol, trade)

    for attempt in range(1, 4):
        try:
            close_result = bot.execution.close_position(symbol, side, amount)
            if _close_result_confirmed(close_result):
                close_ok = True
                break
            last_close_error = "close order not confirmed flat"
            bot.log(f"⚠️ EMERGENCY_CLOSE {symbol}: cierre no confirmado, exposición sigue abierta")
        except Exception as close_error:
            last_close_error = str(close_error)
            bot.log(
                f"⚠️ EMERGENCY_CLOSE intento {attempt}/3 (chase limit) fallido en {symbol}: {close_error}"
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))

    if not close_ok:
        for attempt in range(1, 3):
            try:
                bot.log(f"🧯 EMERGENCY_CLOSE intento MARKET {attempt}/2 en {symbol}")
                market_result = bot.execution.create_reduce_only_market_order(
                    symbol, exit_side, amount
                )
                if _close_result_confirmed(market_result):
                    close_ok = True
                    break
                last_close_error = "market close not confirmed flat"
            except Exception as close_error:
                last_close_error = str(close_error)
                bot.log(
                    f"⚠️ EMERGENCY_CLOSE intento MARKET {attempt}/2 fallido en {symbol}: {close_error}"
                )
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))

    ttr = time.perf_counter() - started
    result["ttr_seconds"] = round(ttr, 6)
    result["last_error"] = last_close_error
    result["close_ok"] = close_ok

    _append_event = append_event_fn if append_event_fn else append_execution_event
    _send_tg = send_telegram_fn if send_telegram_fn else send_telegram_msg

    if close_ok and persist_state and trade:
        with bot.db_lock:
            bot.brain.save_error_snapshot(
                symbol,
                "EMERGENCY_CLOSE_NO_VALID_SL",
                {"sl_error": str(sl_error)[:200] if sl_error else ""},
            )
            bot.brain.delete_active_trade_state(symbol)
        _append_event(
            bot,
            "EMERGENCY_CLOSE_EXECUTED",
            {
                "symbol": symbol,
                "ttr_seconds": round(ttr, 6),
                "sl_error": str(sl_error)[:180] if sl_error else "",
            },
        )
        with bot.lock:
            if symbol in bot.active_trades:
                del bot.active_trades[symbol]
        bot.log(f"🧯 EMERGENCY CLOSE {symbol}: SL inválido por gap, cierre MARKET ejecutado")

    if not close_ok and halt_on_failure:
        bot.is_paused = True
        bot.integrity_lock_active = True
        setattr(bot, "halt_system_active", True)
        if trade:
            trade["status"] = "EMERGENCY_CLOSE_PENDING"
            trade["closing_in_progress"] = True
            with bot.db_lock:
                bot.brain.save_active_trade_state(symbol, trade)
        bot.log(
            f"☢️ FALLO CRÍTICO {symbol}: no se pudo adjuntar SL ni cerrar por mercado tras 3 intentos."
        )
        _append_event(
            bot,
            "EMERGENCY_CLOSE_FAILED_HALT",
            {
                "symbol": symbol,
                "sl_error": str(sl_error)[:180] if sl_error else "",
                "close_error": last_close_error[:180],
            },
        )
        try:
            _send_tg(
                "🚨 *FALLO CRÍTICO DE PROTECCIÓN*\n"
                f"Símbolo: {symbol}\n"
                "No fue posible adjuntar HARD SL ni ejecutar Emergency Close tras 3 intentos.\n"
                f"Error SL: {str(sl_error)[:180] if sl_error else 'N/A'}\n"
                f"Error Close: {last_close_error[:180]}\n"
                "🛑 Sistema en HALT manual. No se abrirán nuevas posiciones hasta intervención humana."
            )
        except Exception:
            bot.log(
                f"🚨 ALERTA LOCAL: no se pudo enviar notificación Telegram de HALT para {symbol}."
            )

    return close_ok, result


def _fail_safe_close_when_sl_missing(bot, symbol: str, side: str, amount: float) -> bool:
    close_ok, _result = _emergency_market_close(
        bot=bot,
        symbol=symbol,
        side=side,
        amount=amount,
        verify_flat=True,
        persist_state=False,
        halt_on_failure=False,
    )
    return close_ok


def _validate_entry_preconditions(bot, symbol: str, is_shadow: bool) -> str | None:
    existing_state = (getattr(bot, "active_trades", {}) or {}).get(symbol)
    decision = evaluate_entry_risk_decision(
        bot,
        symbol,
        is_shadow,
        existing_state=existing_state,
        is_trading_halted_fn=shadow_logger.is_trading_halted,
    )
    if decision:
        record_risk_decision(bot, decision, symbol=symbol, is_shadow=is_shadow)
        bot.log(decision.log_message)
        return decision.reason

    return None


def _validate_symbol_entry(bot, symbol: str, is_shadow: bool) -> str | None:
    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()
    if symbol_base in controls.get("blocked", set()):
        bot.log(f"🧱 BLOQUEADO por matriz táctica: {symbol}")
        return "SYMBOL_BLOCKED_MATRIX"

    if not is_shadow:
        execution = getattr(bot, "execution", None)
        is_quarantined = getattr(execution, "is_symbol_quarantined", None)
        get_remaining = getattr(execution, "get_symbol_quarantine_remaining_seconds", None)
        if callable(is_quarantined) and is_quarantined(symbol):
            remaining_s = int(get_remaining(symbol) if callable(get_remaining) else 0)
            bot.log(
                f"🚫 SYMBOL_QUARANTINE_ACTIVE {symbol}: bloqueada apertura real por degradación cancel_all ({remaining_s}s restantes)."
            )
            return "SYMBOL_QUARANTINED"

    return None


def _calculate_pnl_and_metrics(
    trade: dict[str, Any],
    exit_price: float,
    fees: float,
    side: str,
) -> dict[str, Any]:
    amt = float(trade["amount"])
    pnl_core = _calculate_trade_pnl(
        side=side,
        entry_price=float(trade["entry"]),
        exit_price=float(exit_price),
        amount=amt,
        leverage=trade.get("leverage", 1),
        fee_usd=float(fees or 0.0),
        margin_used=trade.get("margin_used"),
        percent_on_margin=bool(trade.get("is_shadow", False) or trade.get("simulated_real", False)),
    )
    pnl_bruto_usd = pnl_core["gross_usd"]
    pnl_neto_usd = pnl_core["net_usd"]
    pnl_neto_percent = pnl_core["net_pct"]

    entry_price = trade["entry"]
    mae_price = trade.get("mae_price", entry_price)
    mfe_price = trade.get("mfe_price", entry_price)

    if side == "BUY":
        mae_percent = ((entry_price - mae_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((mfe_price - entry_price) / entry_price) * 100 if mfe_price else 0
    else:
        mae_percent = ((mae_price - entry_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((entry_price - mfe_price) / entry_price) * 100 if mfe_price else 0

    return {
        "amt": amt,
        "pnl_bruto_usd": pnl_bruto_usd,
        "pnl_neto_usd": pnl_neto_usd,
        "pnl_neto_percent": pnl_neto_percent,
        "mae_percent": mae_percent,
        "mfe_percent": mfe_percent,
    }


def _calculate_margin_used(notional_usd: float, leverage: int | float) -> float:
    lev = _clamp_leverage_1_to_10(leverage)
    return float(notional_usd or 0.0) / max(1, lev)


def _calculate_trade_pnl(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    amount: float,
    leverage: int | float = 1,
    fee_usd: float = 0.0,
    fee_rate: float | None = None,
    margin_used: float | None = None,
    percent_on_margin: bool = False,
) -> dict[str, float]:
    entry = float(entry_price or 0.0)
    exit_val = float(exit_price or 0.0)
    qty = float(amount or 0.0)
    lev = _clamp_leverage_1_to_10(leverage)
    notional = entry * qty
    gross_usd = (exit_val - entry) * qty
    if str(side or "BUY").upper() == "SELL":
        gross_usd *= -1

    if fee_rate is not None:
        fee_total = (entry * qty * float(fee_rate or 0.0)) + (
            exit_val * qty * float(fee_rate or 0.0)
        )
    else:
        fee_total = float(fee_usd or 0.0)

    net_usd = gross_usd - fee_total
    pct_base = float(margin_used or 0.0) if percent_on_margin else notional
    if percent_on_margin and pct_base <= 0.0:
        pct_base = _calculate_margin_used(notional, lev)
    gross_pct = (gross_usd / pct_base) * 100.0 if pct_base > 0 else 0.0
    net_pct = (net_usd / pct_base) * 100.0 if pct_base > 0 else 0.0
    return {
        "gross_usd": gross_usd,
        "net_usd": net_usd,
        "gross_pct": gross_pct,
        "net_pct": net_pct,
        "fee_usd": fee_total,
        "notional_usd": notional,
        "margin_used": pct_base if percent_on_margin else _calculate_margin_used(notional, lev),
    }


def _reserve_simulated_margin(bot, trade: dict[str, Any]) -> tuple[bool, str]:
    margin_used = float(trade.get("margin_used") or 0.0)
    if margin_used <= 0.0:
        return True, "NO_MARGIN_REQUIRED"
    balance_lock = getattr(bot, "balance_lock", None)
    ctx = balance_lock if balance_lock is not None else nullcontext()
    with ctx:
        available = float(getattr(bot, "available_balance", 0.0) or 0.0)
        if available + 1e-9 < margin_used:
            return (
                False,
                f"SIM_BALANCE_INSUFFICIENT available=${available:.2f} margin=${margin_used:.2f}",
            )
        bot.available_balance = available - margin_used
    trade["margin_reserved"] = True
    trade["margin_released"] = False
    return True, "RESERVED"


def _release_simulated_margin(bot, trade: dict[str, Any], pnl_usd: float) -> bool:
    if not (trade.get("is_shadow", False) or trade.get("simulated_real", False)):
        return False
    if trade.get("margin_released", False):
        return False
    margin_used = float(trade.get("margin_used") or 0.0)
    balance_lock = getattr(bot, "balance_lock", None)
    ctx = balance_lock if balance_lock is not None else nullcontext()
    with ctx:
        bot.balance = float(getattr(bot, "balance", 0.0) or 0.0) + float(pnl_usd or 0.0)
        bot.available_balance = (
            float(getattr(bot, "available_balance", 0.0) or 0.0)
            + margin_used
            + float(pnl_usd or 0.0)
        )
    trade["margin_released"] = True
    return True


def _safe_log_signal_alert(bot, **kwargs) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "log_signal_alert", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with lock or nullcontext():
        method(**kwargs)


def _safe_update_signal_alert_status(bot, entry_client_order_id, status) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "update_signal_alert_status", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with lock or nullcontext():
        method(entry_client_order_id, status)


def _order_looks_filled(order: dict) -> bool:
    if not isinstance(order, dict):
        return False
    status = str(order.get("status") or (order.get("info") or {}).get("status") or "").lower()
    return status in {"closed", "filled"}


def _exchange_position_is_flat(bot, symbol: str) -> bool:
    fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
    if not callable(fetch_positions):
        raise RuntimeError("No se puede confirmar exposición cero: fetch_positions no disponible")

    positions = fetch_positions() or []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if normalize_position_symbol(pos.get("symbol", "")) != symbol:
            continue
        contracts = pos.get("contracts")
        if contracts is None:
            contracts = (pos.get("info") or {}).get("positionAmt", 0)
        if abs(float(contracts or 0.0)) > 0.0:
            return False
    return True


def _sanitize_context(bot, context):
    data_service = getattr(bot, "data_service", None)
    sanitizer = getattr(data_service, "sanitize_context", None)
    if callable(sanitizer):
        return sanitizer(context)
    if isinstance(context, dict):
        return dict(context)
    return {}


def _get_local_open_trade_counts(bot):
    """
    Conteo local de trades abiertos (solo para PAPER_MODE).

    FALLBACK POLICY (conservadora):
    Si falla la lectura de active_trades o estados persistidos,
    retornamos los MAXIMOS permitidos. Esto es fail-closed intencional:
    si no sabemos el estado real, evitamos abrir nuevas posiciones
    que podrian causar sobreexposicion.
    """
    open_statuses = open_trade_statuses()
    states = {}
    try:
        states.update(getattr(bot, "active_trades", {}) or {})
    except Exception as error:
        logger = getattr(bot, "log", None)
        if callable(logger):
            logger(f"🛑 No se pudo leer active_trades local para conteo: {error}")
        return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
            getattr(Config, "MAX_SHADOW_TRADES", 0)
        )

    brain = getattr(bot, "brain", None)
    loader = getattr(brain, "load_active_trade_states", None)
    if callable(loader):
        try:
            for symbol, state in (loader() or {}).items():
                states.setdefault(symbol, state)
        except Exception as error:
            logger = getattr(bot, "log", None)
            if callable(logger):
                logger(f"🛑 No se pudo cargar estado persistido para conteo: {error}")
            return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
                getattr(Config, "MAX_SHADOW_TRADES", 0)
            )

    num_real = 0
    num_shadow = 0
    for state in states.values():
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "").upper()
        if status not in open_statuses:
            continue
        if bool(state.get("is_shadow", False)):
            num_shadow += 1
        else:
            num_real += 1
    return num_real, num_shadow

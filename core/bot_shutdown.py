import threading
import time

from config import Config
from core.execution_telemetry import append_execution_event
from core.trade_state import TradeStatus


def _order_type_upper(order: dict) -> str:
    if not isinstance(order, dict):
        return ""
    raw = order.get("type") or (order.get("info") or {}).get("type") or ""
    return str(raw).upper()


def _is_cancelable_shutdown_order(order: dict) -> bool:
    order_type = _order_type_upper(order)
    # Mantener órdenes protectivas de supervivencia (STOP/TP) en libro.
    if "STOP" in order_type or "TAKE_PROFIT" in order_type:
        return False
    # Purgar todas las órdenes de entrada/salida no-protectivas.
    return True


def _safe_cancel_open_orders(bot) -> int:
    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    cancel_order = getattr(bot.execution, "cancel_order", None)
    if not callable(fetch_open_orders) or not callable(cancel_order):
        return 0

    canceled = 0
    try:
        open_orders = fetch_open_orders() or []
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: no se pudieron consultar open orders: {error}")
        return 0

    for order in open_orders if isinstance(open_orders, (list, tuple)) else []:
        try:
            if not _is_cancelable_shutdown_order(order):
                continue
            order_id = str(order.get("id") or "")
            symbol = str(order.get("symbol") or "")
            if not order_id or not symbol:
                continue
            cancel_order(symbol, order_id)
            canceled += 1
        except Exception as error:
            bot.log(f"⚠️ SHUTDOWN: cancel falló {order.get('id', 'N/A')}: {error}")
    return canceled


def _finalize_partial_flags(bot):
    # Si el remanente se canceló en shutdown, la orden de entrada termina cerrada.
    with bot.lock:
        snapshot = dict(getattr(bot, "active_trades", {}) or {})

    partial_status = {TradeStatus.PARTIAL_FILL_PENDING.value, TradeStatus.PARTIAL_FILL.value}
    for symbol, trade in snapshot.items():
        if not isinstance(trade, dict):
            continue
        status = str(trade.get("status") or "").upper()
        if status not in partial_status:
            continue
        trade["remaining_amount"] = 0.0
        trade["partial_fill_pending"] = False
        trade["status"] = TradeStatus.CLOSED.value
        trade["entry_order_status"] = TradeStatus.CLOSED.value
        trade["entry_order_finalized_by_shutdown"] = True
        with bot.db_lock:
            bot.brain.save_active_trade_state(symbol, trade)


def _ensure_survival_hard_sl(bot, deadline_mono: float):
    if bool(getattr(Config, "PAPER_MODE", True)):
        return
    # Reusar la lógica robusta existente de wallet sync para anclar Hard SL.
    for _ in range(2):
        if time.monotonic() >= deadline_mono:
            return
        try:
            bot.sync_wallet()
        except Exception as error:
            bot.log(f"⚠️ SHUTDOWN: sync_wallet falló: {error}")
        time.sleep(0.25)


def _shutdown_sequence(bot, reason: str, logger):
    timeout_s = min(85.0, float(getattr(Config, "SHUTDOWN_TIMEOUT_SECONDS", 75)))
    deadline = time.monotonic() + timeout_s

    bot.stop_requested = True
    bot.halt_system_active = True
    bot.integrity_lock_active = True

    append_execution_event(
        bot,
        "SHUTDOWN_SEQUENCE_START",
        {"reason": reason, "timeout_s": timeout_s},
    )

    # 1) Bloquear nuevos trabajos y loops.
    bot.is_running = False
    shutdown_event = getattr(bot, "_shutdown_event", None)
    if shutdown_event is None:
        shutdown_event = threading.Event()
        bot._shutdown_event = shutdown_event
    shutdown_event.set()

    # 2) Purgar book no-protectivo.
    canceled = _safe_cancel_open_orders(bot)
    _finalize_partial_flags(bot)

    # 3) Asegurar supervivencia (Hard SL real en posiciones llenas).
    _ensure_survival_hard_sl(bot, deadline)

    # 4) Persistencia final y cierre ordenado.
    try:
        if hasattr(bot, "save_cache"):
            bot.save_cache(blocking=True)
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: save_cache falló: {error}")

    try:
        ds = getattr(bot, "data_service", None)
        if ds is not None:
            ds.shutdown(wait=True)
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: data_service shutdown falló: {error}")

    try:
        if getattr(bot, "ws_manager", None):
            bot.ws_manager.stop()
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: ws stop falló: {error}")

    try:
        if getattr(bot, "ui", None):
            bot.ui.stop()
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: ui stop falló: {error}")

    try:
        shadow_logger = getattr(bot, "_shadow_logger", None)
        if shadow_logger:
            shadow_logger.stop()
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: shadow logger stop falló: {error}")

    try:
        loop = getattr(bot, "main_loop", None)
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        loop_thread = getattr(bot, "_main_loop_thread", None)
        if loop_thread is not None and loop_thread.is_alive():
            loop_thread.join(timeout=2.0)
    except Exception as error:
        bot.log(f"⚠️ SHUTDOWN: main loop stop falló: {error}")

    append_execution_event(
        bot,
        "SHUTDOWN_SEQUENCE_DONE",
        {
            "reason": reason,
            "canceled_open_orders": int(canceled),
            "elapsed_s": round(max(0.0, timeout_s - (deadline - time.monotonic())), 3),
        },
    )
    logger.warning("✅ SHUTDOWN_SEQUENCE completada")
    bot.shutdown_complete.set()


def request_graceful_shutdown(bot, reason: str, logger):
    if getattr(bot, "shutdown_in_progress", False):
        return
    if getattr(bot, "shutdown_complete", None) is None:
        bot.shutdown_complete = threading.Event()
    bot.shutdown_in_progress = True

    worker = threading.Thread(
        target=_shutdown_sequence,
        args=(bot, reason, logger),
        daemon=False,
        name="shutdown-sequence",
    )
    bot._shutdown_thread = worker
    worker.start()

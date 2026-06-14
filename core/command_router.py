from core.commands.api_status import _handle_api_status_commands
from core.commands.audit import _handle_audit_commands
from core.commands.history import _handle_history_commands
from core.commands.intelligence import _handle_intelligence_commands
from core.commands.ops import (
    _handle_misc_commands,
    _handle_training_and_maintenance_commands,
    _help_message,
)


def handle_basic_command(bot, text: str) -> bool:
    if _handle_api_status_commands(bot, text):
        return True

    if _handle_audit_commands(bot, text):
        return True

    if _handle_intelligence_commands(bot, text):
        return True

    if _handle_history_commands(bot, text):
        return True

    if _handle_misc_commands(bot, text):
        return True

    if _handle_training_and_maintenance_commands(bot, text):
        return True

    if text == "/help":
        from tools.notifier import send_telegram_msg

        send_telegram_msg(_help_message())
        return True

    if text in ["/on", "/resume"]:
        from tools.notifier import send_telegram_msg

        if bot.mandatory_train_pending:
            send_telegram_msg(
                "🛡️ *MODO DEFENSIVO ACTIVO*: No se puede reanudar sin re-entrenamiento. Use /force_train."
            )
        else:
            bot.is_paused = False
            send_telegram_msg("🟢 *SISTEMA ACTIVO*")
        return True

    if text in ["/off", "/pause"]:
        from tools.notifier import send_telegram_msg

        bot.is_paused = True
        send_telegram_msg("🟡 *SISTEMA EN PAUSA*")
        return True

    if text in ["/panic", "/closeall"]:
        from tools.notifier import send_telegram_msg

        bot.is_paused = True
        bot._close_all_positions_emergency()
        send_telegram_msg("🔴 *EMERGENCIA*: Todo cerrado en Binance.")
        return True

    if text == "/reset":
        from tools.notifier import send_telegram_msg

        msg = bot.handle_reset_pnl()
        send_telegram_msg(msg)
        return True

    if text == "/rebase_capital":
        from tools.notifier import send_telegram_msg

        try:
            unsafe_reasons = []
            with bot.lock:
                if bool(getattr(bot, "halt_system_active", False)):
                    unsafe_reasons.append("HALT_SYSTEM_ACTIVE")
                if bool(getattr(bot, "integrity_lock_active", False)):
                    unsafe_reasons.append("INTEGRITY_LOCK_ACTIVE")
                for symbol, trade in getattr(bot, "active_trades", {}).items():
                    if not (trade or {}).get("is_shadow", False):
                        status = str((trade or {}).get("status") or "")
                        unsafe_reasons.append(f"LOCAL_REAL_TRADE:{symbol}:{status or 'UNKNOWN'}")
            fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
            if callable(fetch_positions):
                positions = fetch_positions() or []
                open_positions = [
                    p for p in positions if abs(float((p or {}).get("contracts") or 0.0)) > 0.0
                ]
                if open_positions:
                    unsafe_reasons.append("EXCHANGE_POSITIONS_OPEN")
            fetch_open_orders = getattr(getattr(bot, "execution", None), "fetch_open_orders", None)
            if callable(fetch_open_orders):
                open_orders = fetch_open_orders() or []
                if open_orders:
                    unsafe_reasons.append("EXCHANGE_OPEN_ORDERS")
            if unsafe_reasons:
                send_telegram_msg(
                    "🛑 *REBASE CAPITAL BLOQUEADO*\n"
                    "No se liberan locks con estado REAL inseguro. Usa /recover_halt tras reconciliar.\n"
                    f"Motivos: {', '.join(unsafe_reasons[:6])}"
                )
                return True

            current = float(bot.get_current_balance() or 0.0)
            balance_lock = getattr(bot, "balance_lock", None)
            if balance_lock:
                with balance_lock:
                    bot.balance = current
                    bot.daily_initial_balance = current
            else:
                bot.balance = current
                bot.daily_initial_balance = current
            with bot.lock:
                bot.peak_pnl = 0.0
                bot.integrity_lock_active = False
                bot.circuit_breaker_active = False
                bot.daily_drawdown_alert_sent = False
                bot.is_paused = False
            send_telegram_msg(
                f"✅ *REBASE CAPITAL OK*\nNuevo ancla: ${current:.2f}\nIntegrity lock liberado."
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error en /rebase_capital: {error}")
        return True

    if text == "/recover_halt":
        from core.reconciliation import recover_halt_if_exchange_consistent
        from tools.notifier import send_telegram_msg

        ok, message = recover_halt_if_exchange_consistent(bot)
        prefix = "✅" if ok else "🛑"
        send_telegram_msg(f"{prefix} *RECOVER HALT*\n{message}")
        return True

    if text == "/test":
        from tools.notifier import send_telegram_msg

        send_telegram_msg(
            "🔔 *PRUEBA DE CONEXIÓN*\nSi estás leyendo esto, las notificaciones de Sniper AI funcionan correctamente."
        )
        return True

    return False

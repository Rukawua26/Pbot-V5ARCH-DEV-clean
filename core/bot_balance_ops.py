import time

from config import Config
from core.trade_helpers import persist_simulated_wallet_state, restore_simulated_available_balance


def start_silent_sync(bot):
    """Bucle que sincroniza el balance con Binance cada 1 hora."""
    while bot.is_running:
        try:
            balance_lock = getattr(bot, "balance_lock", None)
            if Config.PAPER_MODE:
                if not bool(getattr(bot, "_simulated_wallet_initialized", False)):
                    if balance_lock:
                        with balance_lock:
                            if not float(getattr(bot, "balance", 0.0) or 0.0):
                                bot.balance = Config.PAPER_INITIAL_BALANCE
                    else:
                        if not float(getattr(bot, "balance", 0.0) or 0.0):
                            bot.balance = Config.PAPER_INITIAL_BALANCE
                    if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                        bot.daily_initial_balance = Config.PAPER_INITIAL_BALANCE
                    restore_simulated_available_balance(bot)
                    persist_simulated_wallet_state(bot)
                time.sleep(3600)
                continue

            actual_balance = get_current_balance(bot)

            with bot.lock:
                # Si no hay trades abiertos, el balance interno debe ser igual al de la API
                has_active_trades = bool(bot.active_trades)
            if not has_active_trades:
                if balance_lock:
                    with balance_lock:
                        bot.balance = actual_balance
                else:
                    bot.balance = actual_balance
                bot.log(f"🔄 SYNC: Balance sincronizado silenciosamente: ${actual_balance:.2f}")
                bot.brain.log_equity(actual_balance)  # Registrar punto en la curva

            time.sleep(3600)  # Espera 1 hora
        except Exception as error:
            bot.log(f"⚠️ Error en sincronización de balance: {error}")
            time.sleep(60)


def get_current_balance(bot):
    """Obtiene el balance total en USDT desde Binance (v118)."""
    try:
        return bot.execution.get_balance()
    except Exception as error:
        bot.log(f"⚠️ Error obteniendo balance: {error}")
        if not Config.PAPER_MODE:
            bot.is_paused = True
            bot.integrity_lock_active = True
            setattr(bot, "halt_system_active", True)
            raise RuntimeError(f"REAL_BALANCE_UNAVAILABLE: {error}") from error
        return getattr(bot, "available_balance", 0.0)


def handle_reset_pnl(bot):
    """Limpia el historial de hoy y resetea el balance inicial."""
    try:
        # 1. Ejecutar rotación de historial (Mantenimiento de 3 meses)
        bot.brain.rotate_history(days_to_keep=90)
        bot.brain.reset_daily_stats()
        current_balance = get_current_balance(bot)
        balance_lock = getattr(bot, "balance_lock", None)
        if balance_lock:
            with balance_lock:
                bot.balance = current_balance
                bot.daily_initial_balance = bot.balance
        else:
            bot.balance = current_balance
            bot.daily_initial_balance = bot.balance
        if Config.PAPER_MODE:
            persist_simulated_wallet_state(bot)

        with bot.lock:
            # --- FIX: RESET COMPLETO DE ESTADO ---
            bot.peak_pnl = 0.0
            bot.circuit_breaker_active = False
            bot.daily_drawdown_alert_sent = False
            bot._drawdown_warning_sent = False
            bot._circuit_breaker_alert_sent = False
            bot.current_target = Config.DAILY_GOALS[0]  # Reiniciar meta al 5%

        bot.log("♻️ SISTEMA REINICIADO: Historial rotado y balance inicial fijado.")
        return f"🔄 *PNL RESETEADO:* Balance inicial fijado en ${bot.balance:.2f}. Meta reiniciada al 5.0%. Todo limpio para hoy."
    except Exception as error:
        return f"⚠️ Error Reset PnL: {error}"

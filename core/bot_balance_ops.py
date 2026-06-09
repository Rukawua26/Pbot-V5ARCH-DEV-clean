import time

from config import Config


def start_silent_sync(bot):
    """Bucle que sincroniza el balance con Binance cada 1 hora."""
    while bot.is_running:
        try:
            if Config.PAPER_MODE:
                if not float(getattr(bot, "balance", 0.0) or 0.0):
                    bot.balance = Config.PAPER_INITIAL_BALANCE
                if not float(getattr(bot, "available_balance", 0.0) or 0.0):
                    bot.available_balance = Config.PAPER_INITIAL_BALANCE
                if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                    bot.daily_initial_balance = Config.PAPER_INITIAL_BALANCE
                time.sleep(3600)
                continue

            with bot.lock:
                # Obtener balance real de la API (ATÓMICO)
                actual_balance = get_current_balance(bot)

                # Si no hay trades abiertos, el balance interno debe ser igual al de la API
                if not bot.active_trades:
                    bot.balance = actual_balance
                    bot.log(f"🔄 SYNC: Balance sincronizado silenciosamente: ${actual_balance:.2f}")
                    bot.brain.log_equity(bot.balance)  # Registrar punto en la curva

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
        return getattr(bot, "available_balance", 0.0)


def handle_reset_pnl(bot):
    """Limpia el historial de hoy y resetea el balance inicial."""
    try:
        # 1. Ejecutar rotación de historial (Mantenimiento de 3 meses)
        bot.brain.rotate_history(days_to_keep=90)
        bot.brain.reset_daily_stats()
        bot.balance = get_current_balance(bot)
        bot.daily_initial_balance = bot.balance

        with bot.lock:
            bot.balance = get_current_balance(bot)
        # --- FIX: RESET COMPLETO DE ESTADO ---
        bot.peak_pnl = 0.0
        bot.circuit_breaker_active = False
        bot.daily_drawdown_alert_sent = False
        bot.current_target = Config.DAILY_GOALS[0]  # Reiniciar meta al 5%

        bot.log("♻️ SISTEMA REINICIADO: Historial rotado y balance inicial fijado.")
        return f"🔄 *PNL RESETEADO:* Balance inicial fijado en ${bot.balance:.2f}. Meta reiniciada al 5.0%. Todo limpio para hoy."
    except Exception as error:
        return f"⚠️ Error Reset PnL: {error}"

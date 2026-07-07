from config import Config
from core.risk_policy import activate_runtime_protection


def check_safety_and_goals(bot, current_pnl=None):
    base_bal = bot.daily_initial_balance if bot.daily_initial_balance > 0 else bot.balance
    _ = base_bal

    if current_pnl is None:
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            log_message="🛑 Daily PnL no verificable. Proteccion runtime activada.",
            reason="DAILY_PNL_UNVERIFIED",
            source="runtime_safety",
        )
        return False

    if current_pnl > bot.peak_pnl:
        bot.peak_pnl = current_pnl

    # 1. Trailing Stop de Cuenta: Si perdemos 3% desde el punto más alto del día
    if bot.peak_pnl > 0 and (bot.peak_pnl - current_pnl) >= Config.DAILY_TRAILING_STOP:
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            log_message=(
                f"⚠️ Trailing Stop: Protegiendo {current_pnl:.2f}% (Caída del 3% desde el pico)"
            ),
            reason="DAILY_TRAILING_STOP_HIT",
            source="runtime_safety",
            extra={"current_pnl": float(current_pnl), "peak_pnl": float(bot.peak_pnl)},
        )
        return False

    # 2. Límite de Pérdida Diaria: -3% desde el inicio
    if current_pnl <= -Config.DAILY_LOSS_LIMIT:
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            pause=True,
            mandatory_train_pending=True,
            log_message=(
                f"💀 Límite diario alcanzado: {current_pnl:.2f}%. MODO DEFENSIVO ACTIVADO."
            ),
            telegram_message=(
                "🛡️ *MODO DEFENSIVO ACTIVADO*\nPérdida diaria límite alcanzada. "
                "El bot requiere re-entrenamiento para continuar."
            ),
            alert_once_attr="daily_loss_limit_alert_sent",
            reason="DAILY_LOSS_LIMIT_REACHED",
            source="runtime_safety",
            extra={"current_pnl": float(current_pnl)},
        )
        return False

    # 3. Gestión de Metas (5% -> 10% -> 15%)
    for goal in Config.DAILY_GOALS:
        if current_pnl >= goal and bot.current_target == goal:
            bot.log(f"🚀 Meta de {goal}% alcanzada.")
            try:
                next_idx = Config.DAILY_GOALS.index(goal) + 1
                if next_idx < len(Config.DAILY_GOALS):
                    bot.current_target = Config.DAILY_GOALS[next_idx]
                else:
                    bot.circuit_breaker_active = True  # Meta final 15% alcanzada
            except Exception as error:
                bot.log(f"⚠️ No se pudo avanzar meta diaria {goal}: {error}")
    return True

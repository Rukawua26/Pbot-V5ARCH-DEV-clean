import logging

from config import Config
from core.execution_telemetry import append_execution_event
from core.risk_policy import activate_runtime_protection

try:
    from tools.notifier import send_telegram_msg
except Exception:
    send_telegram_msg = None  # type: ignore[assignment]


def _notify_drawdown_warning(bot, current_pnl: float, threshold_pct: float) -> None:
    """Envia alerta proactiva cuando el drawdown supera el 80% del limite diario."""
    if bool(getattr(bot, "_drawdown_warning_sent", False)):
        return
    bot._drawdown_warning_sent = True
    msg = (
        f"DRAWDOWN WARNING: {current_pnl:.2f}% consumido del limite diario "
        f"({threshold_pct:.2f}%). Zona de amortiguacion activada."
    )
    bot.log(msg)
    append_execution_event(
        bot,
        "DAILY_DRAWDOWN_WARNING",
        {
            "component": "RiskEngine",
            "event": "DAILY_DRAWDOWN_WARNING",
            "current_pnl_pct": float(current_pnl),
            "threshold_pct": float(threshold_pct),
            "severity": "WARNING",
        },
    )
    try:
        if callable(send_telegram_msg):
            send_telegram_msg(msg)
    except Exception as error:
        logging.getLogger("SniperAI").debug("drawdown warning telegram failed: %s", error)


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

    # 1. Trailing Stop de Cuenta: Si perdemos 3% desde el punto mas alto del dia
    if bot.peak_pnl > 0 and (bot.peak_pnl - current_pnl) >= Config.DAILY_TRAILING_STOP:
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            log_message=(
                f"⚠️ Trailing Stop: Protegiendo {current_pnl:.2f}% (Caida del 3% desde el pico)"
            ),
            reason="DAILY_TRAILING_STOP_HIT",
            source="runtime_safety",
            extra={"current_pnl": float(current_pnl), "peak_pnl": float(bot.peak_pnl)},
        )
        return False

    # 2. Zona de amortiguacion: alerta proactiva al 80% del limite diario
    drawdown_warning_threshold = -float(Config.DAILY_LOSS_LIMIT) * 0.80
    if current_pnl <= drawdown_warning_threshold and current_pnl > -float(Config.DAILY_LOSS_LIMIT):
        _notify_drawdown_warning(bot, current_pnl, drawdown_warning_threshold)

    # 3. Limite de Perdida Diaria: -3% desde el inicio
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

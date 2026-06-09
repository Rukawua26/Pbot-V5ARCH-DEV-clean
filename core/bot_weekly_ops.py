from datetime import UTC, datetime

from core.strategy.utils import StrategyUtils
from tools.notifier import send_telegram_msg

MAINTENANCE_WEEKDAY_UTC = 4  # Friday
MAINTENANCE_HOUR_UTC = 10


def check_weekly_schedule(bot, module_available_fn):
    """Envía el reporte de evolución los domingos a las 20:00."""
    now_local = datetime.now()
    if now_local.weekday() == 6 and now_local.hour == 20 and now_local.minute == 0:
        if not bot._weekly_sent:
            try:
                if module_available_fn("evolution_logger"):
                    from evolution_logger import get_evolution_report

                    report = get_evolution_report()
                    send_telegram_msg(f"📊 *RESUMEN DE CRECIMIENTO SEMANAL*\n\n{report}")
                else:
                    bot.log("ℹ️ Resumen semanal omitido: evolution_logger no disponible.")
            except Exception as error:
                bot.log(f"⚠️ Error en reporte semanal: {error}")
            bot._weekly_sent = True
    elif now_local.hour != 20:
        bot._weekly_sent = False


def check_weekly_maintenance_utc(bot):
    """Viernes 10:00 UTC: purga DB y limpia cachés transitorios una vez por semana."""
    now_utc = datetime.now(UTC)
    maintenance_key = f"{now_utc.isocalendar().year}-W{now_utc.isocalendar().week}"

    if bot._last_weekly_maintenance_utc == maintenance_key:
        return
    if not _weekly_maintenance_due(now_utc):
        return

    bot.log("🧹 Mantenimiento semanal DB (viernes 10:00 UTC): iniciando purge+VACUUM...")
    result = bot.brain.weekly_maintenance(shadow_days_to_keep=30, signal_days_to_keep=30)
    if result.get("error"):
        bot.log(f"⚠️ Mantenimiento DB falló: {result['error']}")
    else:
        caches_cleared = _clear_transient_runtime_caches(bot)
        bot.log(
            "✅ Mantenimiento DB OK: "
            f"shadow_deleted={result.get('shadow_deleted', 0)} "
            f"signal_deleted={result.get('signal_deleted', 0)} "
            f"cutoff={result.get('cutoff')} "
            f"vacuum={result.get('vacuum_ok', False)} "
            f"caches_cleared={caches_cleared}"
        )
    bot._last_weekly_maintenance_utc = maintenance_key


def _weekly_maintenance_due(now_utc):
    if now_utc.weekday() > MAINTENANCE_WEEKDAY_UTC:
        return True
    if now_utc.weekday() < MAINTENANCE_WEEKDAY_UTC:
        return False
    return now_utc.hour >= MAINTENANCE_HOUR_UTC


def _clear_transient_runtime_caches(bot):
    cleared = []

    candle_cache = getattr(bot, "candle_cache", None)
    if candle_cache is not None and hasattr(candle_cache, "clear"):
        candle_cache.clear()
        cleared.append("candle_cache")

    if getattr(StrategyUtils, "_ob_cache", None):
        StrategyUtils._ob_cache.clear()
        cleared.append("orderbook_cache")

    data_service = getattr(bot, "data_service", None)
    if data_service is not None:
        if getattr(data_service, "data_cache", None):
            data_service.data_cache.clear()
            cleared.append("data_cache")
        if getattr(data_service, "last_ohlcv_fetch", None):
            data_service.last_ohlcv_fetch.clear()
            cleared.append("ohlcv_fetch_ts")

    if getattr(bot, "_funding_rate_cache", None):
        bot._funding_rate_cache.clear()
        cleared.append("funding_rate_cache")

    if getattr(bot, "_btc_data_cache", None) is not None:
        bot._btc_data_cache = None
        bot._btc_data_cache_ts = 0
        cleared.append("btc_data_cache")

    if getattr(bot, "_market_cache", None):
        bot._market_cache = {}
        bot._market_cache_ts = 0
        cleared.append("market_cache")

    return ",".join(cleared) if cleared else "none"

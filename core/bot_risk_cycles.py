import time

from config import Config
from tools.notifier import send_telegram_msg


def _btc_price_is_fresh(bot) -> bool:
    max_age = float(getattr(Config, "BTC_RISK_MAX_PRICE_AGE_SECONDS", 90.0) or 90.0)
    ts = float(getattr(bot, "market_btc_price_ts", 0.0) or 0.0)
    if ts <= 0:
        return False
    return (time.monotonic() - ts) <= max_age


def run_crash_predictor_cycle(bot) -> bool:
    if not (
        Config.CRASH_DETECTION_ENABLED
        and hasattr(bot, "market_btc_price")
        and bot.market_btc_price > 0
    ):
        return False

    if not _btc_price_is_fresh(bot):
        bot.log(
            "⚠️ Crash Predictor omitido: precio BTC sin timestamp fresco. "
            "Manteniendo estado defensivo existente."
        )
        return False

    try:
        btc_price = bot.market_btc_price
        btc_delta_tf = getattr(bot, "market_btc_change_tf", 0)

        crash_result = bot.crash_predictor.analyze_crash_risk(
            df=None,
            symbol="BTC",
            funding_rate=0,
            side="BUY",
            order_book=None,
            btc_delta_tf=btc_delta_tf,
            btc_price=btc_price,
            btc_ema_200=0,
        )

        if crash_result and crash_result.get("recommended_action") == "CLOSE_ALL":
            bot.log(
                f"🚨 CRASH INMINENTE (Prob: {crash_result.get('crash_probability', 0):.0f}%) - ¡EJECUTANDO VETO DE EMERGENCIA!"
            )
            closed_count = bot._close_all_positions_emergency()
            bot.log(f"🛡️ PROTOCOLO COMPLETADO: {closed_count} posiciones liquidadas a mercado.")

            send_telegram_msg(
                f"🚨 *ALERTA CRASH*\nProtocolo de emergencia activado. {closed_count} posiciones cerradas de inmediato por seguridad."
            )
            bot.circuit_breaker_active = True
            time.sleep(10)
            return True

        if crash_result and crash_result.get("recommended_action") == "REDUCE_EXPOSURE":
            bot.log(
                f"⚠️ TURBULENCIA DETECTADA: {crash_result.get('crash_probability', 0):.0f}% - Restringiendo apalancamiento."
            )

    except Exception as error:
        bot.log(
            f"❌ FATAL: El sistema Crash Predictor ha fallado en el loop principal. Error: {error}"
        )
        import traceback

        bot.log(traceback.format_exc())

    return False


def run_btc_panic_cycle(bot):
    bot.btc_panic = bot.force_btc_panic
    try:
        if not _btc_price_is_fresh(bot):
            bot.log("⚠️ BTC Panic omitido: precio BTC stale o sin timestamp fresco")
            return

        btc_data = bot._get_cached_btc_data()
        if btc_data is not None and len(btc_data) >= 2:
            last_close = btc_data["close"].iloc[-1]
            prev_close = btc_data["close"].iloc[-2]
            btc_change = (last_close - prev_close) / prev_close * 100
            bot.market_btc_change_tf = btc_change

            if btc_change < -Config.BTC_PANIC_DROP_PERCENT:
                bot.btc_panic = True
                if time.time() - getattr(bot, "last_panic_alert", 0) > 300:
                    bot.log(
                        f"🚨 BTC PANIC DETECTADO ({btc_change:.2f}%). Bloqueando COMPRAS, permitiendo SHORTS."
                    )
                    send_telegram_msg(
                        f"🚨 *BTC PANIC FILTER*\nBitcoin ha caído un {btc_change:.2f}% en 1h. Modo SOLO VENTAS activado."
                    )
                    bot.last_panic_alert = time.time()
    except Exception as error:
        bot.log(f"⚠️ Error en BTC Panic Filter: {error}")

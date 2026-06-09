import time

import ccxt


def heartbeat_loop(bot):
    while bot.is_running:
        exchange = bot.execution.exchange  # Copia local para evitar condición de carrera
        if exchange is not None:
            try:
                exchange.fetch_status()
                bot.api_status = "🟢 ONLINE"
            except (ccxt.NetworkError, ccxt.ExchangeError) as error:
                bot.api_status = "🔴 OFFLINE"
                bot.log(f"⚠️ API Heartbeat falló: {error}")
            except Exception as error:
                bot.api_status = "🔴 OFFLINE"
                bot.log(f"❌ Error crítico en heartbeat: {error}")
        else:
            bot.api_status = "🔴 OFFLINE"
        time.sleep(30)


def check_instinctive_safety(bot, symbol, context):
    """Bloquea entradas reales ante volatilidad extrema (v104.0)."""
    # --- CUARENTENA SELECTIVA ---
    try:
        atr_pct = context.get("atr_pct", 0) * 100
        # Si el ATR_PCT (volatilidad relativa) es muy alto (>5%)
        if atr_pct > 5.0:
            bot.log(f"⚠️ GAP/VOL detectado en {symbol} ({atr_pct:.2f}%). Forzando MODO SHADOW.")
            return "FORCE_SHADOW"
    except Exception as error:
        bot.log(f"⚠️ Error en validación de safety para {symbol}: {error}")
    return "OK"


def close_all_positions_emergency(bot):
    """Cierra todas las posiciones activas inmediatamente."""
    count = 0
    with bot.lock:
        symbols = list(bot.active_trades.keys())

    for symbol in symbols:
        with bot.lock:
            trade = bot.active_trades.get(symbol)
            price = trade.get("last_price", 0) if trade else 0

        if trade:
            bot.close_trade(symbol, "EMERGENCY PANIC", price)
            count += 1
    return count

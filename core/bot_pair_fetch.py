import time


def fetch_pair_data(bot, symbol):
    """Helper para fetch paralelo [V118-PRO] con reintentos agresivos."""
    start_time = time.time()
    data = (None, None)

    # En scan priorizamos throughput: si un símbolo se degrada, preferimos saltarlo
    # y no bloquear el ciclo completo por retries largos.
    max_retries = 0

    for attempt in range(max_retries + 1):
        try:
            # Intentar fetch secuencial
            df_main = bot.data_service.fetch_and_update_data(symbol, "1h", fast_mode=True)

            # Verificación rápida
            min_candles = 50
            if df_main is None or (hasattr(df_main, "__len__") and len(df_main) < min_candles):
                # Si falla timeframe principal, no vale la pena seguir
                if attempt < max_retries:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                # Fallo final
                bot._update_scanner_status(symbol, "❌ NO_DATA", qoe="--")
                return (
                    symbol,
                    (None, None),
                    int((time.time() - start_time) * 1000),
                )

            df_4h = bot.data_service.fetch_and_update_data(symbol, "4h", fast_mode=True)

            data = (df_main, df_4h)
            break  # Éxito

        except Exception as error:
            if attempt < max_retries:
                time.sleep(0.1 * (attempt + 1))
            else:
                bot.log(f"⚠️ Error fatal en hilo {symbol}: {error}")
                bot._update_scanner_status(symbol, "❌ ERROR", qoe="--")

    # Garantizamos que se registre la latencia aunque falle
    elapsed = int((time.time() - start_time) * 1000)
    return symbol, data, elapsed

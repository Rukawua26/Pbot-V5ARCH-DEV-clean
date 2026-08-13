import asyncio
import concurrent.futures
import math
import os
import time

import ccxt
import pandas as pd
import ta.trend as ta_trend

from config import Config
from core.market_intelligence import (
    build_operable_targets,
    get_candidate_pool_limit,
)
from core.time_utils import monotonic_now
from tools.notifier import send_telegram_msg


def _btc_cache_marker(df):
    if df is None or df.empty:
        return None
    if "time" in df.columns:
        return df["time"].iloc[-1]
    return len(df)


def _log_indicator_fallback_error(bot, indicator: str, error) -> None:
    if time.time() - float(getattr(bot, "_sentiment_fallback_log_ts", 0.0)) > 300:
        bot._sentiment_fallback_log_ts = time.time()
        bot.log(f"⚠️ Fallback {indicator} falló: {error}")


def _get_cached_btc_indicator(bot, btc_1h, indicator: str):
    cache = getattr(bot, "_btc_indicator_fallback_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        bot._btc_indicator_fallback_cache = cache

    marker = _btc_cache_marker(btc_1h)
    key = (indicator, marker)
    if key in cache:
        return cache[key]

    if indicator == "EMA_200":
        close_vals = btc_1h["close"].dropna()
        if len(close_vals) < 200:
            return None
        value = ta_trend.EMAIndicator(close_vals, window=200).ema_indicator().iloc[-1]
    elif indicator == "ADX_14":
        high_vals = btc_1h["high"].dropna()
        low_vals = btc_1h["low"].dropna()
        close_vals = btc_1h["close"].dropna()
        if len(high_vals) < 14 or len(low_vals) < 14 or len(close_vals) < 14:
            return None
        min_len = min(len(high_vals), len(low_vals), len(close_vals))
        value = (
            ta_trend.ADXIndicator(
                high_vals.iloc[-min_len:],
                low_vals.iloc[-min_len:],
                close_vals.iloc[-min_len:],
                window=14,
            )
            .adx()
            .iloc[-1]
        )
    else:
        return None

    for old_key in [old_key for old_key in cache if old_key[0] == indicator]:
        cache.pop(old_key, None)
    cache[key] = value
    return value


def _resolve_btc_market_indicators(bot, btc_1h):
    ema_200 = btc_1h["EMA_200"].iloc[-1] if "EMA_200" in btc_1h.columns else None
    adx_14 = btc_1h["ADX_14"].iloc[-1] if "ADX_14" in btc_1h.columns else None

    if ema_200 is None or pd.isna(ema_200):
        try:
            if "close" in btc_1h.columns:
                ema_200 = _get_cached_btc_indicator(bot, btc_1h, "EMA_200")
        except Exception as error:
            _log_indicator_fallback_error(bot, "EMA_200", error)

    if adx_14 is None or pd.isna(adx_14):
        try:
            if {"high", "low", "close"}.issubset(btc_1h.columns):
                adx_14 = _get_cached_btc_indicator(bot, btc_1h, "ADX_14")
        except Exception as error:
            _log_indicator_fallback_error(bot, "ADX_14", error)

    return ema_200, adx_14


def _resolve_triage_worker_count(top_triage_count: int) -> int:
    if top_triage_count <= 0:
        return 1

    cpu_count = os.cpu_count() or 2
    dynamic_default = max(4, min(16, cpu_count * 2))
    configured_cap = int(getattr(Config, "TRIAGE_MAX_WORKERS", dynamic_default) or dynamic_default)
    safe_cap = max(1, min(32, configured_cap))
    return max(1, min(top_triage_count, safe_cap))


def _consume_late_task_exception(task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


def run_market_refresh_cycle(bot):
    if time.time() - getattr(bot, "last_market_update", 0) > 43200:
        bot.log("🎯 Actualizando lista de objetivos (Ciclo 12h)...")
        bot.acquire_targets()
        bot.last_market_update = time.time()


def run_triage_cycle(bot):
    raw_snapshot = bot._get_active_market_snapshot(pool_limit=get_candidate_pool_limit(bot))
    tickers = {item["symbol"]: item["ticker"] for item in raw_snapshot}
    tickers.update(getattr(bot, "_snapshot_tickers", {}))

    triage_snapshot = build_operable_targets(bot, raw_snapshot)
    new_triage_symbols = [item["symbol"] for item in triage_snapshot]
    if new_triage_symbols:
        bot.pairs_to_scan = new_triage_symbols
    elif raw_snapshot:
        bot.pairs_to_scan = []

    return triage_snapshot, tickers


def run_market_context_cycle(bot, tickers):
    base_bal = bot.daily_initial_balance if bot.daily_initial_balance > 0 else bot.balance
    pnl_real_hoy, _ = bot.brain.get_daily_real_pnl(base_bal)
    if pnl_real_hoy is None:
        pnl_real_hoy = 0.0

    ws_btc_price = 0.0
    ws_btc_age = float("inf")
    with bot.price_lock:
        try:
            ws_btc_price = float(getattr(bot, "live_prices", {}).get("BTCUSDT", 0.0) or 0.0)
        except (TypeError, ValueError):
            ws_btc_price = 0.0
        ws_btc_ts = float(getattr(bot, "live_prices_ts", {}).get("BTCUSDT", 0.0) or 0.0)
        if ws_btc_ts > 0:
            ws_btc_age = monotonic_now() - ws_btc_ts

    if ws_btc_price > 0 and ws_btc_age <= float(getattr(Config, "WS_TICKER_MAX_AGE_SECONDS", 15.0)):
        bot.market_btc_price = ws_btc_price
        bot.market_btc_price_source = "WS_TICKER"
        bot.market_btc_price_ts = ws_btc_ts
    else:
        btc_ticker = tickers.get(
            "BTC/USDT:USDT", tickers.get("BTC/USDT", {"last": bot.market_btc_price})
        )
        bot.market_btc_price = float(btc_ticker.get("last", bot.market_btc_price))
        bot.market_btc_price_source = "REST_TICKER"
        bot.market_btc_price_ts = monotonic_now()

    if bot.market_btc_price == 0:
        try:
            bot.log("📡 Recatando precio de BTC manualmente...")
            btc_t = bot.execution.fetch_ticker("BTC/USDT")
            bot.market_btc_price = float(btc_t["last"])
            bot.market_btc_price_ts = monotonic_now()
        except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, ValueError) as error:
            bot.log(f"⚠️ No se pudo rescatar BTC manualmente: {error}")

    try:
        btc_1h = bot._get_cached_btc_data()
        if btc_1h is not None and not btc_1h.empty and len(btc_1h) >= 200:
            has_valid_data = (
                btc_1h["close"].notna().sum() > 0
                and btc_1h["high"].notna().sum() > 0
                and btc_1h["low"].notna().sum() > 0
            )
            if not has_valid_data:
                raise ValueError("Datos de BTC no válidos")

            ema_200, adx_14 = _resolve_btc_market_indicators(bot, btc_1h)

            if not isinstance(bot.market_btc_price, (int, float)) or bot.market_btc_price <= 0:
                raise ValueError(f"market_btc_price inválido: {bot.market_btc_price}")
            if not isinstance(ema_200, (int, float)) or pd.isna(ema_200):
                raise ValueError(f"ema_200 inválido: {ema_200}")
            if not isinstance(adx_14, (int, float)) or pd.isna(adx_14):
                raise ValueError(f"adx_14 inválido: {adx_14}")

            if adx_14 < 20:
                new_sentiment, sentiment_color = "🟡 RANGO", "yellow"
            elif bot.market_btc_price > ema_200:
                new_sentiment, sentiment_color = "🟢 TENDENCIA ALCISTA", "green"
            else:
                new_sentiment, sentiment_color = "🔴 TENDENCIA BAJISTA", "red"

            if new_sentiment != bot.current_sentiment[0]:
                if "RANGO" in new_sentiment and "TENDENCIA" in bot.current_sentiment[0]:
                    send_telegram_msg(
                        "⚠️ *SUGERENCIA DE ESTRATEGIA*\nEl mercado ha entrado en RANGO. En el modelo institucional actual se mantiene motor *1H* con veto macro *4H*."
                    )

                bot.log(f"🌍 CAMBIO DE SENTIMIENTO: {new_sentiment}")
                send_telegram_msg(
                    f"🌍 *RADAR DE SENTIMIENTO*\nEl mercado ha pasado a: *{new_sentiment}*"
                )
                bot.current_sentiment = (new_sentiment, sentiment_color)
    except Exception as error:
        import traceback

        bot.log(f"⚠️ Error en Radar de Sentimiento: {error}")
        bot.log(f"📋 Traceback: {traceback.format_exc(limit=3)}")

    # --- Global market metrics (satellite, fail-silent) ---
    try:
        if hasattr(bot, "global_market_provider") and bot.global_market_provider.enabled:
            bot.global_market_cache = bot.global_market_provider.fetch_global_metrics()
    except Exception:
        bot.log("⚠️ GlobalMarketProvider fetch falló (fail-silent)")

    return pnl_real_hoy


def prepare_top_triage(bot, triage_snapshot):
    triage_snapshot = [
        item
        for item in triage_snapshot
        if bot.latency_quarantine.get(item["symbol"], 0) < time.time()
    ]
    top_triage = triage_snapshot[: Config.TOP_TRIAGE_COUNT]
    if not top_triage:
        return []

    for entry in triage_snapshot[Config.TOP_TRIAGE_COUNT :]:
        bot._update_scanner_status(entry["symbol"], "⏸️ BAJO RVOL", qoe="--")

    top_symbols = [entry["symbol"] for entry in top_triage]
    if hasattr(bot, "ws_manager") and bot.ws_manager:
        bot.ws_manager.update_symbols(top_symbols)

    for entry in top_triage:
        bot.update_radar(
            entry["symbol"],
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚡",
            "⚡ PROCESANDO...",
            {"tier": "IRON"},
        )

    bot.log(f"⚡ TRIAJE PARALELO: Disparando {len(top_triage)} hilos para datos frescos...")
    return top_triage


async def _fetch_triage_data_async(bot, top_triage):
    results = {}
    timeout_s = float(getattr(Config, "TRIAGE_TIMEOUT_SECONDS", 4)) + 1.0
    max_workers = _resolve_triage_worker_count(len(top_triage))
    semaphore = asyncio.Semaphore(max_workers)
    inflight = getattr(bot, "_triage_fetch_tasks", None)
    if not isinstance(inflight, dict):
        inflight = {}
        bot._triage_fetch_tasks = inflight

    for symbol, task in list(inflight.items()):
        if not task.done():
            continue
        try:
            task.result()
        except (asyncio.CancelledError, Exception) as error:
            bot.log(f"⚠️ Fetch tardío finalizó con error para {symbol}: {error}")
        inflight.pop(symbol, None)

    async def _fetch_one(item):
        symbol_raw = item.get("symbol_raw", item["symbol"])
        sym_map = item["symbol"]
        async with semaphore:
            existing = inflight.get(sym_map)
            if existing is not None and not existing.done():
                return sym_map, None, "IN_FLIGHT"
            if len(inflight) >= max_workers:
                return sym_map, None, "CAPACITY"
            task = asyncio.create_task(
                asyncio.to_thread(bot._fetch_pair_data, symbol_raw),
                name=f"triage-fetch:{sym_map}",
            )
            task.add_done_callback(_consume_late_task_exception)
            inflight[sym_map] = task
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=timeout_s,
                )
                await asyncio.sleep(0)
                return sym_map, result, None
            except TimeoutError:
                return sym_map, None, "TIMEOUT"
            except Exception as error:
                return sym_map, None, error
            finally:
                if task.done():
                    inflight.pop(sym_map, None)

    fetched = await asyncio.gather(*[_fetch_one(item) for item in top_triage])

    timeout_count = 0
    for sym_map, result, fetch_error in fetched:
        if fetch_error is None and result is not None:
            try:
                _, data, elapsed = result
                results[sym_map] = {"data": data, "elapsed": elapsed}
            except Exception as e_thread:
                bot.log(f"⚠️ Error devuelto por tarea async para {sym_map}: {e_thread}")
                results[sym_map] = {"data": None, "elapsed": -1, "error": "TASK_ERROR"}
                bot.update_radar(
                    sym_map,
                    {"signal": "WAIT", "mode": "NONE"},
                    0.0,
                    "❌",
                    "❌ ERROR HILO",
                    {"tier": "IRON"},
                    response_ms=-1,
                )
        elif fetch_error in {"TIMEOUT", "IN_FLIGHT", "CAPACITY"}:
            timeout_count += 1
            results[sym_map] = {"data": None, "elapsed": -1, "error": str(fetch_error)}
            bot.update_radar(
                sym_map,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⏱️",
                "⏱️ TIMEOUT HILO",
                {"tier": "IRON"},
                response_ms=-1,
            )
        else:
            bot.log(f"⚠️ Error async en fetch para {sym_map}: {fetch_error}")
            results[sym_map] = {"data": None, "elapsed": -1, "error": "FETCH_ERROR"}
            bot.update_radar(
                sym_map,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "❌",
                "❌ ERROR HILO",
                {"tier": "IRON"},
                response_ms=-1,
            )

    if timeout_count > 0:
        bot.log(
            f"⏱️ TRIAJE TIMEOUT: {timeout_count} (of {len(top_triage)}) tareas no terminaron a tiempo."
        )

    return results


def fetch_triage_data_parallel(bot, top_triage):
    if getattr(bot, "main_loop", None) is not None and bot.main_loop.is_running():
        timeout_s = float(getattr(Config, "TRIAGE_TIMEOUT_SECONDS", 4)) + 1.0
        max_workers = _resolve_triage_worker_count(len(top_triage))
        worker_waves = max(1, math.ceil(len(top_triage) / max_workers))
        future = asyncio.run_coroutine_threadsafe(
            _fetch_triage_data_async(bot, top_triage),
            bot.main_loop,
        )
        try:
            return future.result(timeout=(timeout_s * worker_waves) + 2.0)
        except concurrent.futures.TimeoutError:
            future.cancel()
            bot.log("⏱️ TRIAJE OUTER TIMEOUT: ciclo async cancelado; fetches tardíos acotados.")
            return {
                item["symbol"]: {"data": None, "elapsed": -1, "error": "OUTER_TIMEOUT"}
                for item in top_triage
            }

    bot.log("⚠️ TRIAJE LOOP UNAVAILABLE: ciclo omitido hasta restaurar main_loop.")
    return {
        item["symbol"]: {"data": None, "elapsed": -1, "error": "LOOP_UNAVAILABLE"}
        for item in top_triage
    }


def finalize_scan_cycle(bot, signal_stats):
    if bot.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
        bot.scanner_history.sort(key=lambda x: 0 if x.get("signal") == "SELL" else 1)

    total_scanned = signal_stats["BUY"] + signal_stats["SELL"] + signal_stats["NEUTRAL"]
    if total_scanned > 0:
        buy_pct = (signal_stats["BUY"] / total_scanned) * 100
        sell_pct = (signal_stats["SELL"] / total_scanned) * 100
        neutral_pct = (signal_stats["NEUTRAL"] / total_scanned) * 100
        bot.log(
            f"📊 Señales: BUY {signal_stats['BUY']} ({buy_pct:.0f}%) | "
            f"SELL {signal_stats['SELL']} ({sell_pct:.0f}%) | "
            f"NEUTRAL {signal_stats['NEUTRAL']} ({neutral_pct:.0f}%) | "
            f"Veredictos: ✅{signal_stats['REAL']} 🧪{signal_stats['SHADOW']} ❌{signal_stats['VETO']}"
        )
        bot.last_signal_stats = signal_stats

    bot._maybe_send_daily_exit_scorecard()

    suffix = bot.self_adjust_exigency()
    valid_signals = [
        item for item in bot.scanner_history if "OK" in item["result"] or "SHADOW" in item["result"]
    ]
    if not valid_signals:
        if bot.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
            bot.ai_status_msg = f"🛡️ PROTECCIÓN: MERCADO HOSTIL{suffix}"
        elif bot.current_sentiment[0] == "🟡 TENDENCIA NEUTRAL":
            bot.ai_status_msg = f"🟡 RANGO: SIN CALIDAD{suffix}"
        else:
            bot.ai_status_msg = f"🔍 ESCANEANDO OPORTUNIDADES{suffix}"
    else:
        bot.ai_status_msg = f"🎯 RADAR: {len(valid_signals)} SEÑALES ACTIVAS{suffix}"

    if time.time() - getattr(bot, "last_cache_save", 0) > 300:
        bot.save_cache()
        bot.log("💾 Memoria guardada.")
        bot.last_cache_save = time.time()


def run_cycle_wait_and_api_log(bot):
    time.sleep(Config.SCAN_INTERVAL)
    if time.time() - getattr(bot, "_api_weight_logged_time", 0) > 60:
        weight = 0
        if bot.weight_tracker:
            weight = bot.weight_tracker.get_current_weight()
        bot.log(f"⚖️ API Weight (1 min): {weight}")
        if bot.weight_tracker:
            status = bot.weight_tracker.get_status()
            bot.log(
                f"📊 API Usage: {status['usage_pct']}% | Remaining: {status['remaining']} | Level: {status['level']}"
            )
        bot._api_weight_logged_time = time.time()

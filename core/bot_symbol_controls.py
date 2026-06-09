import json
import os
import subprocess
import sys
import time


def load_runtime_symbol_controls(bot):
    now = time.time()
    cache = getattr(bot, "_symbol_controls_cache", None)
    if cache and (now - cache.get("loaded_at", 0.0) < 60.0):
        return cache

    controls_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "data_storage",
        "symbol_controls.json",
    )
    controls_path = os.path.abspath(controls_path)

    blocked = set()
    preferred = set()
    reduced = set()

    try:
        if os.path.exists(controls_path):
            with open(controls_path, encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            blocked = {
                str(symbol).split("/")[0] for symbol in payload.get("blocked_symbols", []) if symbol
            }
            preferred = {
                str(symbol).split("/")[0]
                for symbol in payload.get("preferred_symbols", [])
                if symbol
            }
            reduced = {
                str(symbol).split("/")[0] for symbol in payload.get("reduced_symbols", []) if symbol
            }
    except Exception as error:
        bot.log(f"⚠️ Error leyendo symbol_controls.json: {error}")

    cache = {
        "blocked": blocked,
        "preferred": preferred,
        "reduced": reduced,
        "loaded_at": now,
    }
    bot._symbol_controls_cache = cache
    return cache


def refresh_symbol_controls_if_due(bot):
    now = time.time()
    if now - getattr(bot, "_symbol_controls_last_refresh", 0.0) < float(
        max(60, bot._symbol_controls_refresh_interval)
    ):
        return

    script_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "tools",
        "generate_symbol_controls.py",
    )
    script_path = os.path.abspath(script_path)
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0:
            bot._symbol_controls_last_refresh = now
            bot._symbol_controls_cache["loaded_at"] = 0.0
            bot.log("✅ Symbol controls refreshed from decision matrix.")
        else:
            error = (proc.stderr or proc.stdout or "unknown error").strip()
            bot.log(f"⚠️ Error refreshing symbol controls: {error[:180]}")
    except Exception as error:
        bot.log(f"⚠️ Exception refreshing symbol controls: {error}")


def get_cached_funding_rate(bot, symbol):
    """
    Fetch funding rate con cache de 5 min.
    El funding rate cambia cada 8h, no tiene sentido fetchearlo cada ciclo.
    """
    now = time.time()
    cached = bot._funding_rate_cache.get(symbol)
    if cached:
        rate, ts = cached
        if now - ts < bot._funding_cache_ttl:
            return rate

    try:
        funding_rate = bot.execution.fetch_funding_rate(symbol)
        rate = float(funding_rate.get("fundingRate", 0))
        bot._funding_rate_cache[symbol] = (rate, now)
        return rate
    except Exception:
        return 0.0


def get_cached_btc_data(bot):
    """
    Fetch BTC data una vez por ciclo. Evita 3 fetches duplicados.
    """
    now = time.time()
    if bot._btc_data_cache is not None and now - bot._btc_data_cache_ts < 60:
        return bot._btc_data_cache

    try:
        btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        bot._btc_data_cache = btc_data
        bot._btc_data_cache_ts = now
        return btc_data
    except Exception:
        return None

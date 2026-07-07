import hashlib
import json
import random
import time

from config import Config
from core.execution_telemetry import append_execution_event
from core.telegram_api import sanitize_telegram_error, telegram_get_json
from core.time_utils import monotonic_now, parse_datetime_utc, utc_now


def _extract_telegram_message(update):
    if not isinstance(update, dict):
        return {}
    return update.get("message") or update.get("edited_message") or update.get("channel_post") or {}


def _is_authorized_telegram_chat(chat_id, from_id=None) -> bool:
    expected = str(getattr(Config, "TELEGRAM_CHAT_ID", "") or "").strip()
    current = str(chat_id or "").strip()
    if not bool(expected) or current != expected:
        return False
    admin_ids = str(getattr(Config, "TELEGRAM_ADMIN_IDS", "") or "").strip()
    is_group_chat = expected.startswith("-")
    if is_group_chat and not admin_ids:
        return False
    if admin_ids:
        if from_id is None:
            return False
        allowed = [x.strip() for x in admin_ids.split(",") if x.strip()]
        if allowed and str(from_id) not in allowed:
            return False
    return True


def _telegram_command_name(text: str) -> str:
    return str(text or "").split(maxsplit=1)[0][:64]


def _telegram_chat_id_hash(chat_id) -> str:
    raw = str(chat_id or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _apply_ticker_stream_update(bot, data) -> int:
    if not isinstance(data, list):
        return 0

    updated = 0
    now_ts = monotonic_now()
    with bot.price_lock:
        live_prices_ts = getattr(bot, "live_prices_ts", None)
        if live_prices_ts is None:
            bot.live_prices_ts = {}
            live_prices_ts = bot.live_prices_ts

        for ticker in data:
            symbol = str(ticker.get("s", "") or "")
            close_price = ticker.get("c")
            if not symbol or close_price is None:
                continue

            bot.live_prices[symbol] = close_price
            live_prices_ts[symbol] = now_ts
            updated += 1

            if symbol == "BTCUSDT":
                try:
                    btc_price = float(close_price)
                except (TypeError, ValueError):
                    continue
                if btc_price > 0:
                    bot.market_btc_price = btc_price
                    bot.market_btc_price_source = "WS_TICKER"
                    bot.market_btc_price_ts = now_ts

    return updated


def websocket_monitor(bot):
    """Hilo dedicado a escuchar precios en tiempo real vía Websockets (v106.5)."""
    try:
        import websocket
    except ImportError:
        bot.log("⚠️ 'websocket-client' no instalado. Usando polling REST (más lento).")
        return

    def on_message(ws, message):
        try:
            data = json.loads(message)
            # Formato !ticker@arr: [{'s': 'BTCUSDT', 'c': '60000.00'}, ...]
            _apply_ticker_stream_update(bot, data)
        except (KeyError, ValueError, json.JSONDecodeError):
            return  # Mensaje malformado, ignorar
        except Exception as error:
            bot.log(f"⚠️ Error procesando mensaje WS: {error}")

    is_reconnecting = False
    reconnect_delay = 5.0

    def on_open(ws):
        nonlocal is_reconnecting, reconnect_delay
        if is_reconnecting:
            bot.log("⚡ WEBSOCKET: Reconectado exitosamente. Precios en tiempo real restaurados.")
        else:
            bot.log("⚡ WEBSOCKET: Conectado. Precios en tiempo real activos.")
        is_reconnecting = False
        reconnect_delay = 5.0

    while bot.is_running:
        try:
            websocket.enableTrace(False)
            ws = websocket.WebSocketApp(
                "wss://fstream.binance.com/ws/!ticker@arr",
                on_message=on_message,
                on_open=on_open,
            )
            ws.run_forever()
            if bot.is_running:
                is_reconnecting = True
                wait_s = reconnect_delay + random.uniform(0.0, 1.0)
                bot.log(f"🔌 WEBSOCKET: Conexión cerrada. Reintentando en {wait_s:.1f}s...")
                time.sleep(wait_s)
                reconnect_delay = min(reconnect_delay * 1.8, 60.0)
        except Exception as error:
            if not is_reconnecting:
                bot.log(f"🔌 WEBSOCKET: Desconectado. Reintentando... (Error: {error})")
            is_reconnecting = True
            wait_s = reconnect_delay + random.uniform(0.0, 1.0)
            time.sleep(wait_s)
            reconnect_delay = min(reconnect_delay * 1.8, 60.0)


def telegram_listener(bot):
    """Escucha comandos como /report o /train desde Telegram."""
    last_update_id = 0
    backoff_seconds = 5
    while bot.is_running:
        try:
            if not Config.TELEGRAM_TOKEN:
                time.sleep(10)
                continue

            response = telegram_get_json(
                "getUpdates",
                params={"offset": last_update_id, "timeout": 30},
                timeout=35,
            )

            for update in response.get("result", []):
                last_update_id = update["update_id"] + 1
                message = _extract_telegram_message(update)
                text = str(message.get("text", "") or "").strip()
                chat_id = message.get("chat", {}).get("id")
                from_id = (message.get("from") or {}).get("id")

                if not text:
                    continue
                if not _is_authorized_telegram_chat(chat_id, from_id):
                    append_execution_event(
                        bot,
                        "TELEGRAM_COMMAND_REJECTED",
                        {
                            "command": _telegram_command_name(text),
                            "chat_id_hash": _telegram_chat_id_hash(chat_id),
                            "reason": "unauthorized_chat",
                        },
                    )
                    continue

                append_execution_event(
                    bot,
                    "TELEGRAM_COMMAND_ACCEPTED",
                    {
                        "command": _telegram_command_name(text),
                        "chat_id_hash": _telegram_chat_id_hash(chat_id),
                    },
                )
                # Lógica centralizada
                bot.handle_command(text)

            backoff_seconds = 5

        except Exception as error:
            now_ts = monotonic_now()
            if now_ts - float(getattr(bot, "_telegram_last_err_log", 0.0)) > 120:
                bot._telegram_last_err_log = now_ts
                bot.log(f"Telegram Error: {sanitize_telegram_error(error)}")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60)
            continue
        time.sleep(1)


def perform_post_mortem(bot):
    """Analiza trades cerrados hace 15m para etiquetar falsos positivos."""
    try:
        with bot.db_lock:
            pending = bot.brain.get_trades_pending_post_mortem()
        now = utc_now()
        for trade in pending:
            try:
                close_time = parse_datetime_utc(trade["timestamp"])
                if (now - close_time).total_seconds() < 900:
                    continue  # Esperar 15 min

                ticker = bot.execution.fetch_ticker(trade["symbol"])
                curr_price = float(ticker["last"])

                verdict = "NEUTRAL"
                if trade["pnl_percent"] < 0:
                    # Si perdimos y el precio siguió en contra -> La señal fue un Falso Positivo
                    if trade["side"] == "BUY" and curr_price < trade["exit_price"]:
                        verdict = "FALSE_POSITIVE"
                    elif trade["side"] == "SELL" and curr_price > trade["exit_price"]:
                        verdict = "FALSE_POSITIVE"
                    else:
                        verdict = "BAD_TIMING"

                with bot.db_lock:
                    bot.brain.update_post_mortem(
                        trade["id"], {"price_15m": curr_price, "verdict": verdict}
                    )
                if verdict == "FALSE_POSITIVE":
                    bot.log(
                        f"💀 Post-Mortem {trade['symbol']}: Confirmado Falso Positivo. Aprendiendo..."
                    )
            except Exception:
                continue
    except Exception as error:
        bot.log(f"⚠️ Error Post-Mortem: {error}")

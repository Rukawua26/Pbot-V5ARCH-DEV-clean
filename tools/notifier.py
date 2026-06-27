"""
SNIPER AI - NOTIFIER MODULE v118.2
===================================
Módulo de notificaciones para Telegram con reintentos y cola.
"""

import time
import threading
import queue
import itertools
from enum import Enum
from collections.abc import Callable
from config import Config
from core.telegram_api import sanitize_telegram_error, telegram_post


class Priority(Enum):
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5


NotificationCallback = Callable[[str, dict], None]


class SatelliteNotifier:
    """Callback notifier for read-only satellite modules."""

    def __init__(self, callbacks=None):
        self._callbacks: list[NotificationCallback] = list(callbacks or [])

    def register(self, callback: NotificationCallback) -> None:
        self._callbacks.append(callback)

    def notify(self, event: str, payload: dict) -> None:
        for callback in list(self._callbacks):
            try:
                callback(event, dict(payload))
            except Exception:
                continue


class NotificationQueue:
    """Cola de notificaciones con rate limiting."""

    def __init__(self, max_retries=3, rate_limit_seconds=1):
        self.queue = queue.PriorityQueue()
        self.max_retries = max_retries
        self.rate_limit = rate_limit_seconds
        self.last_sent = 0
        self.running = True
        self._sequence = itertools.count()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        while self.running:
            try:
                item = self.queue.get(timeout=0.5)
                if item is None or item[2] is None:
                    break
                _, _, method, payload, retries, priority = item
                self._send_with_retry(method, payload, retries, priority)
            except queue.Empty:
                continue

    def _send_with_retry(self, method, payload, retries, priority=Priority.INFO):
        for attempt in range(retries):
            try:
                # Rate limiting
                now = time.time()
                elapsed = now - self.last_sent
                if priority.value < Priority.ERROR.value and elapsed < self.rate_limit:
                    time.sleep(self.rate_limit - elapsed)

                request_kwargs = {"timeout": 10}
                if "json" in payload:
                    request_kwargs["json"] = payload["json"]
                else:
                    request_kwargs["data"] = payload.get("data", {})
                    request_kwargs["files"] = payload.get("files", {})
                    request_kwargs["timeout"] = 15

                response = telegram_post(method, **request_kwargs)
                if response.status_code == 200:
                    self.last_sent = time.time()
                    return True
                elif response.status_code == 429:  # Rate limited
                    time.sleep(2**attempt)  # Exponential backoff
                elif (
                    response.status_code == 400
                    and (
                        payload.get("json", {}).get("parse_mode") == "Markdown"
                        or payload.get("data", {}).get("parse_mode") == "Markdown"
                    )
                ):
                    if "json" in payload:
                        fallback_json = dict(payload["json"])
                        fallback_json.pop("parse_mode", None)
                        response = telegram_post(method, json=fallback_json, timeout=10)
                    else:
                        fallback_data = dict(payload.get("data", {}))
                        fallback_data.pop("parse_mode", None)
                        response = telegram_post(
                            method,
                            data=fallback_data,
                            files=payload.get("files", {}),
                            timeout=15,
                        )
                    if response.status_code == 200:
                        self.last_sent = time.time()
                        return True
                    break
                else:
                    break
            except Exception as e:
                if attempt == retries - 1:
                    print(
                        f"⚠️ Telegram send failed after {retries} attempts: {sanitize_telegram_error(e)}"
                    )
        return False

    def send(self, message, priority=Priority.INFO):
        if not _telegram_configured():
            return

        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        self.queue.put(
            (
                -priority.value,
                next(self._sequence),
                "sendMessage",
                {"json": payload},
                self.max_retries,
                priority,
            )
        )

    def send_photo(
        self,
        caption,
        photo_bytes,
        filename="sniper.png",
        priority=Priority.INFO,
    ):
        if not _telegram_configured():
            return

        data = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "Markdown",
        }
        files = {"photo": (filename, photo_bytes, "image/png")}
        self.queue.put(
            (
                -priority.value,
                next(self._sequence),
                "sendPhoto",
                {"data": data, "files": files},
                self.max_retries,
                priority,
            )
        )

    def stop(self):
        self.running = False
        self.queue.put((0, next(self._sequence), None, None, 0, Priority.INFO))
        self.thread.join()


# Instancia global
_notifier_queue = None
_telegram_config_warning_sent = False


def _telegram_configured():
    global _telegram_config_warning_sent
    if Config.TELEGRAM_TOKEN and Config.TELEGRAM_CHAT_ID:
        return True
    if not _telegram_config_warning_sent:
        _telegram_config_warning_sent = True
        print("⚠️ Telegram no configurado: faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    return False


def get_queue():
    global _notifier_queue
    if _notifier_queue is None:
        _notifier_queue = NotificationQueue(
            rate_limit_seconds=float(
                getattr(Config, "TELEGRAM_RATE_LIMIT_SECONDS", 1.2) or 1.2
            )
        )
    return _notifier_queue


def send_telegram_msg(message, priority=Priority.INFO):
    """Envía un mensaje a Telegram con cola y reintentos."""
    try:
        get_queue().send(message, priority)
    except Exception as e:
        print(f"⚠️ Telegram Error: {sanitize_telegram_error(e)}")


def send_telegram_photo(caption, photo_buffer, priority=Priority.INFO):
    """Envía una foto a Telegram."""
    try:
        if not _telegram_configured():
            return
        if hasattr(photo_buffer, "getvalue"):
            photo_bytes = photo_buffer.getvalue()
        else:
            photo_bytes = photo_buffer.read()
        get_queue().send_photo(caption, photo_bytes, priority=priority)
    except Exception as e:
        print(f"⚠️ Telegram Photo Error: {sanitize_telegram_error(e)}")


def notify_trade(symbol, side, pnl_percent, is_shadow):
    """Notifica un trade cerrado."""
    emoji = "🧪" if is_shadow else "🔥"
    sign = "+" if pnl_percent >= 0 else ""
    mode = "SHADOW" if is_shadow else "REAL"
    winner = "✅ WINNER" if pnl_percent > 0 else "❌ STOP LOSS"

    message = f"""
{emoji} *{winner}*
━━━━━━━━━━━━━━━━━━━━
🔹 *Par:* {symbol}
🔹 *Lado:* {side}
🔹 *Modo:* {mode}
📈 *PnL:* {sign}{pnl_percent:.2f}%
━━━━━━━━━━━━━━━━━━━━
"""
    send_telegram_msg(message, Priority.INFO if pnl_percent > 0 else Priority.WARNING)


def notify_panic(reason):
    """Notifica estado de pánico."""
    message = f"""
🚨 *SNIPER PANIC MODE*

*Reason:* {reason}
*Action:* Operaciones reales pausadas
"""
    send_telegram_msg(message, Priority.CRITICAL)


def notify_daily_summary(wins, losses, pnl_percent, target_hit):
    """Resumen diario."""
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0

    status = "🎯 *META ALCANZADA*" if target_hit else "⏳ *EN PROGRESO*"

    message = f"""
📊 *DAILY SUMMARY*

*Trades:* {total} ({wins}W / {losses}L)
*Win Rate:* {wr:.1f}%
*PnL:* {pnl_percent:+.2f}%
{status}
"""
    send_telegram_msg(message, Priority.INFO)

from __future__ import annotations

from config import Config
from core.execution_telemetry import append_execution_event
from core.runtime_metrics import append_runtime_metric
from core.time_utils import monotonic_now
from tools.notifier import Priority, send_telegram_msg


def _looks_like_auth_failure(error: Exception) -> bool:
    message = str(error).lower()
    markers = (
        "api-key",
        "api key",
        "apikey",
        "signature",
        "permission",
        "permissions",
        "unauthorized",
        "invalid api",
        "invalid key",
        "authentication",
        "-2014",
        "-2015",
    )
    return any(marker in message for marker in markers)


def _halt_for_auth_failure(bot, error: Exception) -> None:
    bot.is_paused = True
    bot.integrity_lock_active = True
    setattr(bot, "halt_system_active", True)
    payload = {"error": str(error)[:220], "source": "real_auth_healthcheck"}
    append_execution_event(bot, "REAL_AUTH_HEALTHCHECK_FAILED_HALT", payload)
    append_runtime_metric("halt", {"reason": "REAL_AUTH_HEALTHCHECK_FAILED", **payload})
    bot.log(f"🛑 REAL_AUTH_HEALTHCHECK_FAILED: {error}")
    send_telegram_msg(
        "🛑 *REAL AUTH HEALTHCHECK FAILED*\n"
        "Credenciales/permisos Binance fallaron durante runtime. HALT activado.",
        Priority.CRITICAL,
    )


def maybe_check_real_auth(bot, now_mono: float | None = None) -> bool:
    """Return True when runtime may continue; False means HALT was activated."""
    if Config.PAPER_MODE:
        return True
    interval = float(getattr(Config, "REAL_AUTH_HEALTHCHECK_INTERVAL_SECONDS", 300) or 300)
    if interval <= 0:
        return True
    now = monotonic_now() if now_mono is None else float(now_mono)
    last = float(getattr(bot, "_last_real_auth_healthcheck_mono", 0.0) or 0.0)
    if last and (now - last) < interval:
        return True
    bot._last_real_auth_healthcheck_mono = now

    try:
        bot.execution.fetch_balance()
        append_runtime_metric(
            "real_auth_healthcheck",
            {"ok": True, "interval_seconds": interval},
        )
        return True
    except Exception as error:
        append_runtime_metric(
            "real_auth_healthcheck",
            {
                "ok": False,
                "error_type": type(error).__name__,
                "error": str(error)[:180],
                "auth_like": _looks_like_auth_failure(error),
            },
        )
        if _looks_like_auth_failure(error):
            _halt_for_auth_failure(bot, error)
            return False
        bot.log(f"⚠️ REAL auth healthcheck transitorio: {error}")
        return True

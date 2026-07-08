from __future__ import annotations

import time

from config import Config
from core.execution_telemetry import append_execution_event
from core.reconciliation import reconcile_bootstrap_state
from core.risk_policy import activate_runtime_protection


def handle_ws_reconnected(bot, *, source: str, reconnect_count: int | None = None) -> None:
    """Reconcile exchange state after a market-data WebSocket reconnect.

    The market stream reconnect itself is not authoritative for live exposure.
    In REAL mode, close the blind spot by reconciling positions/orders via REST
    before allowing new entries to proceed. PAPER/SHADOW keep this observational.
    """
    payload = {
        "component": "WebSocket",
        "event": "WS_RECONNECTED",
        "source": str(source),
        "mode": "PAPER" if Config.PAPER_MODE else "REAL",
        "reconnect_count": int(reconnect_count or 0),
    }
    append_execution_event(bot, "WS_RECONNECTED", payload)

    if Config.PAPER_MODE:
        append_execution_event(
            bot,
            "WS_RECONCILE_SKIPPED",
            {**payload, "event": "WS_RECONCILE_SKIPPED", "reason": "PAPER_MODE"},
        )
        return

    now = time.monotonic()
    min_interval = float(getattr(Config, "WS_RECONCILE_MIN_INTERVAL_SECONDS", 30.0) or 30.0)
    last = float(getattr(bot, "_last_ws_reconcile_mono", 0.0) or 0.0)
    if last > 0.0 and now - last < min_interval:
        append_execution_event(
            bot,
            "WS_RECONCILE_SKIPPED",
            {
                **payload,
                "event": "WS_RECONCILE_SKIPPED",
                "reason": "DEBOUNCE",
                "elapsed_s": round(now - last, 3),
                "min_interval_s": min_interval,
            },
        )
        return

    if bool(getattr(bot, "ws_reconciliation_in_progress", False)):
        append_execution_event(
            bot,
            "WS_RECONCILE_SKIPPED",
            {**payload, "event": "WS_RECONCILE_SKIPPED", "reason": "ALREADY_RUNNING"},
        )
        return

    bot._last_ws_reconcile_mono = now
    bot.ws_reconciliation_in_progress = True
    append_execution_event(
        bot, "WS_RECONCILE_STARTED", {**payload, "event": "WS_RECONCILE_STARTED"}
    )
    try:
        reconcile_bootstrap_state(bot)
    except Exception as error:
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            pause=True,
            integrity_lock=True,
            halt_system=True,
            log_message=f"🛑 WS_RECONCILE_FAILED_HALT {source}: {error}",
            reason="WS_RECONCILE_FAILED",
            source="ws_reconciliation",
            extra={"ws_source": str(source), "error": str(error)[:220]},
        )
        append_execution_event(
            bot,
            "WS_RECONCILE_HALT",
            {**payload, "event": "WS_RECONCILE_HALT", "error": str(error)[:220]},
        )
        return
    finally:
        bot.ws_reconciliation_in_progress = False

    append_execution_event(bot, "WS_RECONCILE_OK", {**payload, "event": "WS_RECONCILE_OK"})

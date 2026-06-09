from __future__ import annotations

from dataclasses import dataclass

from core.execution_telemetry import append_execution_event
from tools.learning import shadow_logger
from tools.notifier import send_telegram_msg

RISK_ACTION_ALLOW = "ALLOW"
RISK_ACTION_BLOCK = "BLOCK"
RISK_ACTION_HALT = "HALT"
RISK_ACTION_QUARANTINE = "QUARANTINE"
RISK_SCOPE_ALL = "ALL"
RISK_SCOPE_REAL_ONLY = "REAL_ONLY"
RISK_SCOPE_SYMBOL_ONLY = "SYMBOL_ONLY"

RISK_REASON_PRIORITY = {
    "SHUTDOWN_IN_PROGRESS": 100,
    "HALT_SYSTEM_ACTIVE": 95,
    "INTEGRITY_LOCK_ACTIVE": 90,
    "TRADING_HALTED_DB_ERROR": 85,
    "CIRCUIT_BREAKER_PANIC": 80,
    "BOT_PAUSED": 75,
    "RECOVERY_PENDING_STATE": 70,
    "CONFIDENCE_STAGNATION_LOCK": 60,
    "SYMBOL_QUARANTINED": 50,
    "GLOBAL_COOLDOWN": 40,
}


@dataclass(frozen=True)
class EntryRiskDecision:
    action: str
    reason: str
    scope: str
    log_message: str
    source: str = "entry_guard"
    priority: int = 0


def _priority_for(reason: str) -> int:
    return int(RISK_REASON_PRIORITY.get(str(reason or "").upper(), 0))


def record_risk_decision(
    bot,
    decision: EntryRiskDecision,
    *,
    symbol: str | None = None,
    is_shadow: bool | None = None,
    extra: dict | None = None,
) -> None:
    append_execution_event(
        bot,
        "RISK_DECISION",
        {
            "symbol": symbol,
            "is_shadow": is_shadow,
            "action": decision.action,
            "reason": decision.reason,
            "scope": decision.scope,
            "source": decision.source,
            "priority": decision.priority,
            **(extra or {}),
        },
    )


def evaluate_entry_risk_decision(
    bot,
    symbol: str,
    is_shadow: bool,
    *,
    existing_state=None,
    is_trading_halted_fn=None,
) -> EntryRiskDecision | None:
    if bool(getattr(bot, "stop_requested", False)) or bool(
        getattr(bot, "shutdown_in_progress", False)
    ):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="SHUTDOWN_IN_PROGRESS",
            scope=RISK_SCOPE_ALL,
            log_message="🛑 SHUTDOWN_SEQUENCE: nueva entrada rechazada.",
            source="entry_preconditions",
            priority=_priority_for("SHUTDOWN_IN_PROGRESS"),
        )

    if isinstance(existing_state, dict):
        existing_status = str(existing_state.get("status") or "").upper()
        from core.trade_state import open_trade_statuses

        if existing_status in set(open_trade_statuses()):
            return EntryRiskDecision(
                action=RISK_ACTION_BLOCK,
                reason="RECOVERY_PENDING_STATE",
                scope=RISK_SCOPE_ALL,
                log_message=(
                    f"🧷 RECOVERY_GUARD {symbol}: estado pendiente detectado ({existing_status}). "
                    "Se bloquea nueva apertura para evitar duplicado tras reinicio."
                ),
                source="entry_preconditions",
                priority=_priority_for("RECOVERY_PENDING_STATE"),
            )

    halted_fn = is_trading_halted_fn or shadow_logger.is_trading_halted
    if not is_shadow and halted_fn():
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="TRADING_HALTED_DB_ERROR",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message=(
                "🛑 BLOQUEO DE SEGURIDAD: Trading real detenido por fallo persistente de persistencia (DB)."
            ),
            source="entry_preconditions",
            priority=_priority_for("TRADING_HALTED_DB_ERROR"),
        )

    if not is_shadow and bool(getattr(bot, "integrity_lock_active", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="INTEGRITY_LOCK_ACTIVE",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message="🛑 INTEGRITY_LOCK activo: se bloquea apertura de nuevas posiciones reales.",
            source="entry_preconditions",
            priority=_priority_for("INTEGRITY_LOCK_ACTIVE"),
        )

    if not is_shadow and bool(getattr(bot, "halt_system_active", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="HALT_SYSTEM_ACTIVE",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message="🛑 HALT_SYSTEM activo: bloqueando nuevas posiciones reales.",
            source="entry_preconditions",
            priority=_priority_for("HALT_SYSTEM_ACTIVE"),
        )

    if bool(getattr(bot, "confidence_stagnation_lock_active", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="CONFIDENCE_STAGNATION_LOCK",
            scope=RISK_SCOPE_ALL,
            log_message=f"🛑 CONFIDENCE_STAGNATION_LOCK activo: bloqueando nueva entrada {symbol}.",
            source="entry_preconditions",
            priority=_priority_for("CONFIDENCE_STAGNATION_LOCK"),
        )

    return None


def evaluate_runtime_entry_decision(bot, symbol: str, is_shadow: bool) -> EntryRiskDecision | None:
    if bool(getattr(bot, "halt_system_active", False)) and not is_shadow:
        return EntryRiskDecision(
            action=RISK_ACTION_HALT,
            reason="HALT_SYSTEM_ACTIVE",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message="🛑 HALT_SYSTEM activo: bloqueando nuevas posiciones reales.",
            source="runtime_entry_guard",
            priority=_priority_for("HALT_SYSTEM_ACTIVE"),
        )

    if bool(getattr(bot, "integrity_lock_active", False)) and not is_shadow:
        return EntryRiskDecision(
            action=RISK_ACTION_HALT,
            reason="INTEGRITY_LOCK_ACTIVE",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message="🛑 INTEGRITY_LOCK activo: se bloquea apertura de nuevas posiciones reales.",
            source="runtime_entry_guard",
            priority=_priority_for("INTEGRITY_LOCK_ACTIVE"),
        )

    if bool(getattr(bot, "circuit_breaker_active", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_HALT,
            reason="CIRCUIT_BREAKER_PANIC",
            scope=RISK_SCOPE_ALL,
            log_message=f"🛑 CIRCUIT_BREAKER activo: bloqueando nueva entrada {symbol}.",
            source="runtime_entry_guard",
            priority=_priority_for("CIRCUIT_BREAKER_PANIC"),
        )

    if bool(getattr(bot, "is_paused", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="BOT_PAUSED",
            scope=RISK_SCOPE_ALL,
            log_message=f"🛑 BOT_PAUSED activo: bloqueando nueva entrada {symbol}.",
            source="runtime_entry_guard",
            priority=_priority_for("BOT_PAUSED"),
        )

    execution = getattr(bot, "execution", None)
    is_quarantined = getattr(execution, "is_symbol_quarantined", None)
    get_remaining = getattr(execution, "get_symbol_quarantine_remaining_seconds", None)
    if not is_shadow and callable(is_quarantined) and is_quarantined(symbol):
        remaining_s = int(get_remaining(symbol) if callable(get_remaining) else 0)
        return EntryRiskDecision(
            action=RISK_ACTION_QUARANTINE,
            reason="SYMBOL_QUARANTINED",
            scope=RISK_SCOPE_SYMBOL_ONLY,
            log_message=(
                f"🚫 SYMBOL_QUARANTINE_ACTIVE {symbol}: bloqueada apertura real por degradación cancel_all "
                f"({remaining_s}s restantes)."
            ),
            source="runtime_entry_guard",
            priority=_priority_for("SYMBOL_QUARANTINED"),
        )

    return None


def activate_runtime_protection(
    bot,
    *,
    log_message: str,
    telegram_message: str | None = None,
    alert_once_attr: str | None = None,
    circuit_breaker: bool = False,
    pause: bool = False,
    integrity_lock: bool = False,
    halt_system: bool = False,
    mandatory_train_pending: bool = False,
    reason: str = "RUNTIME_PROTECTION",
    source: str = "runtime_protection",
    scope: str = RISK_SCOPE_ALL,
    symbol: str | None = None,
    extra: dict | None = None,
) -> None:
    if circuit_breaker:
        bot.circuit_breaker_active = True
    if pause:
        bot.is_paused = True
    if integrity_lock:
        bot.integrity_lock_active = True
    if halt_system:
        bot.halt_system_active = True
    if mandatory_train_pending:
        bot.mandatory_train_pending = True

    record_risk_decision(
        bot,
        EntryRiskDecision(
            action=RISK_ACTION_HALT
            if (halt_system or circuit_breaker or pause)
            else RISK_ACTION_BLOCK,
            reason=reason,
            scope=scope,
            log_message=log_message,
            source=source,
            priority=_priority_for(reason),
        ),
        symbol=symbol,
        extra=extra,
    )
    bot.log(log_message)

    if telegram_message:
        if alert_once_attr and bool(getattr(bot, alert_once_attr, False)):
            return
        send_telegram_msg(telegram_message)
        if alert_once_attr:
            setattr(bot, alert_once_attr, True)

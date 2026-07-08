from __future__ import annotations

from dataclasses import dataclass

from config import Config
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
    "WS_RECONCILIATION_IN_PROGRESS": 68,
    "CONFIDENCE_STAGNATION_LOCK": 60,
    "SYMBOL_QUARANTINED": 50,
    "GLOBAL_COOLDOWN": 40,
    "NEUTRAL_AGENT_VOTE": 35,
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
    mode = "SHADOW" if is_shadow else ("PAPER" if Config.PAPER_MODE else "REAL")
    append_execution_event(
        bot,
        "RISK_DECISION",
        {
            "component": "Risk",
            "symbol": symbol,
            "mode": mode,
            "is_shadow": is_shadow,
            "decision": decision.action,
            "action": decision.action,
            "reason": decision.reason,
            "scope": decision.scope,
            "source": decision.source,
            "priority": decision.priority,
            **(extra or {}),
        },
    )


def evaluate_neutral_agent_vote_decision(
    symbol: str,
    is_shadow: bool,
    *,
    prob_final: float | None,
    votes: dict | None,
) -> EntryRiskDecision | None:
    """Block execution when the full agent consensus is explicitly neutral.

    A lone 50.0 probability can be a fallback in older paths, so require a
    non-empty votes dict and all available agent votes at 50.0.
    """
    if not votes:
        return None
    try:
        prob = float(prob_final if prob_final is not None else 0.0)
        vote_values = [float(value) for value in votes.values()]
    except (TypeError, ValueError):
        return None

    if not vote_values:
        return None
    if abs(prob - 50.0) > 1e-9:
        return None
    if any(abs(value - 50.0) > 1e-9 for value in vote_values):
        return None

    return EntryRiskDecision(
        action=RISK_ACTION_BLOCK,
        reason="NEUTRAL_AGENT_VOTE",
        scope=RISK_SCOPE_ALL,
        log_message=f"🧭 NEUTRAL_AGENT_VOTE {symbol}: todos los agentes devolvieron 50.0; no se ejecuta.",
        source="neutral_vote_gate",
        priority=_priority_for("NEUTRAL_AGENT_VOTE"),
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

    if not is_shadow and bool(getattr(bot, "ws_reconciliation_in_progress", False)):
        return EntryRiskDecision(
            action=RISK_ACTION_BLOCK,
            reason="WS_RECONCILIATION_IN_PROGRESS",
            scope=RISK_SCOPE_REAL_ONLY,
            log_message=f"🧷 WS_RECONCILIATION_IN_PROGRESS {symbol}: bloqueando nueva entrada REAL hasta reconciliar.",
            source="runtime_entry_guard",
            priority=_priority_for("WS_RECONCILIATION_IN_PROGRESS"),
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

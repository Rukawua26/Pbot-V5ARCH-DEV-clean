import math
import time

from config import Config
from core.trade_keys import has_trade


def _safe_metric_to_int(value, default=0):
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return int(default)
        return int(round(num))
    except Exception:
        return int(default)


def _safe_metric_to_float(value, default=0.0):
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return float(default)
        return float(num)
    except Exception:
        return float(default)


def _consensus_status(prob_pct: float, result: str) -> tuple[str, str]:
    text = str(result or "")
    upper = text.upper()
    if "NEUTRAL_AGENT_VOTE" in upper or abs(prob_pct - 50.0) <= 1e-9:
        return "BLOCKED_NEUTRAL", "NEUTRAL_AGENT_VOTE"
    if "WS_RECONCILIATION_IN_PROGRESS" in upper:
        return "BLOCKED_RISK", "WS_RECONCILIATION_IN_PROGRESS"
    if "VETO" in upper or "BLOQUE" in upper or "RECHAZ" in upper or "NO EJECUTA" in upper:
        return "BLOCKED", text[:120] or "FILTER_VETO"
    if "OK" in upper or "OPEN" in upper or "SHADOW" in upper:
        return "SELECTED", "PASSED"
    return "OBSERVED", text[:120] or "OBSERVED"


def _record_consensus_round(
    bot, symbol, decision, prob_pct, display_verdict, ctx, votos, response_ms
):
    votes = dict(votos or {}) if isinstance(votos, dict) else {}
    weights = {}
    if isinstance(ctx, dict):
        raw_weights = ctx.get("weights") or ctx.get("final_weights") or ctx.get("agent_weights")
        if isinstance(raw_weights, dict):
            weights = {str(k): _safe_metric_to_float(v, 0.0) for k, v in raw_weights.items()}
    status, reason = _consensus_status(prob_pct, display_verdict)
    mode = str(decision.get("mode") or "NONE") if isinstance(decision, dict) else "NONE"
    model_type = str(getattr(bot, "ghost_model_type", "OFF") or "OFF")
    heuristic = bool(getattr(bot, "bootstrap_heuristic_mode", False))
    features_ver = "v3_clean"
    if isinstance(ctx, dict):
        features_ver = str(ctx.get("features_version") or "v3_clean")
    round_entry = {
        "ts": time.time(),
        "symbol": symbol,
        "side": str(decision.get("signal") or "WAIT") if isinstance(decision, dict) else "WAIT",
        "mode": mode,
        "prob_final": round(float(prob_pct), 4),
        "status": status,
        "reason": reason,
        "votes": {str(k): _safe_metric_to_float(v, 50.0) for k, v in votes.items()},
        "weights": weights,
        "risk_gate": {
            "halt_active": bool(getattr(bot, "halt_system_active", False)),
            "integrity_lock": bool(getattr(bot, "integrity_lock_active", False)),
            "circuit_breaker": bool(getattr(bot, "circuit_breaker_active", False)),
            "paused": bool(getattr(bot, "is_paused", False)),
            "ws_reconciliation_in_progress": bool(
                getattr(bot, "ws_reconciliation_in_progress", False)
            ),
        },
        "model_version": {
            "model_type": model_type,
            "bootstrap_heuristic_mode": heuristic,
            "features_version": features_ver,
        },
        "response_ms": _safe_metric_to_float(response_ms, -1.0),
    }
    history = getattr(bot, "consensus_history", None)
    if history is None:
        return
    clock = getattr(bot, "consensus_lock", None)
    if clock:
        with clock:
            history.appendleft(round_entry)
    else:
        history.appendleft(round_entry)


def update_radar(
    bot,
    symbol,
    decision,
    prob_ia,
    ob_status,
    audit_verdict,
    ctx,
    votos=None,
    response_ms=-1,
):
    """Sincroniza los iconos del Radar basados en el modo de la estrategia (v106.5)."""
    mode = decision["mode"]

    # El Fuego (🔥) es la validación final del consenso para dinero REAL
    fuego_status = "✅" if mode == "REAL" and prob_ia >= Config.REAL_CONFIDENCE_MIN else "❌"

    shadow_min_pct = float(getattr(Config, "SHADOW_MODE_MIN", Config.SHADOW_PROB_MIN * 100))

    # El Tubo (🧪) indica si el bot está aprendiendo de esta moneda (Real o Shadow)
    tubo_status = (
        "✅" if mode in ["REAL", "SHADOW"] and prob_ia >= (shadow_min_pct / 100.0) else "❌"
    )

    # Perfil Táctico
    symbol_sector = next(
        (
            key
            for key, values in Config.SECTORS.items()
            if any(item.lower() in symbol.split("/")[0].lower() for item in values)
        ),
        "OTHE",
    )
    atr_val = ctx.get("atr_pct", 0) * 100 if ctx else 0
    atr_icon = "⚡" if atr_val > 3.0 else ("🐢" if atr_val < 1.0 else "📊")
    tactical_view = f"{symbol_sector} | {atr_icon} {atr_val:.1f}%"

    # [FIX] Evitar duplicados: Si el símbolo ya está, lo quitamos para poner el nuevo al inicio
    slock = getattr(bot, "scanner_lock", None)
    if slock:
        with slock:
            bot.scanner_history = [item for item in bot.scanner_history if item["symbol"] != symbol]
    else:
        bot.scanner_history = [item for item in bot.scanner_history if item["symbol"] != symbol]

    # Limpieza de redundancia visual (Solicitud Usuario)
    # Quitamos "SHADOW" o "REAL" del texto ya que existe columna de MODO
    display_verdict = audit_verdict
    for tag in {"🧪 SHADOW", "🚀 OK: REAL", "🧪", "🚀"}:
        display_verdict = display_verdict.replace(tag, "")
    display_verdict = display_verdict.strip()

    # Marcar visualmente posiciones activas en el radar.
    active_lock = getattr(bot, "lock", None)
    if active_lock:
        with active_lock:
            has_position = has_trade(bot.active_trades, symbol)
    else:
        has_position = has_trade(bot.active_trades, symbol)
    if has_position:
        display_verdict = f"⚡ OPEN | {display_verdict}"

    # Obtener información de patrones
    pattern_type = "NEW"
    wr_hist = 0
    try:
        elite_patterns = bot.brain.get_elite_patterns()
        exp_patterns = bot.brain.get_experimental_patterns()
        base = symbol.split("/")[0]

        for pattern in elite_patterns:
            if base in pattern.get("symbol", ""):
                pattern_type = "ELITE"
                wr_hist = pattern.get("win_rate", 0)
                break
        if pattern_type == "NEW":
            for pattern in exp_patterns:
                if base in pattern.get("symbol", ""):
                    pattern_type = "EXP"
                    wr_hist = pattern.get("win_rate", 0)
                    break
    except Exception as error:
        bot.log(f"⚠️ Pattern metadata unavailable para {symbol}: {error}")

    scanner_entry = {
        "symbol": symbol,
        "sector": symbol_sector,
        "tech_checklist": tactical_view,
        "ob": ob_status,
        "ia_prob": f"{prob_ia * 100:.1f}%" if prob_ia > 0 else "---",
        "ia_shadow": tubo_status,
        "ia_real": fuego_status,
        "result": display_verdict,
        "signal": decision["signal"],
        "side": decision["signal"],
        "rsi_val": (
            _safe_metric_to_int(ctx.get("rsi", {}).get("val", 0))
            if isinstance(ctx.get("rsi"), dict)
            else _safe_metric_to_int(ctx.get("rsi", 0))
        )
        if ctx
        else 0,
        "adx_val": (
            _safe_metric_to_int(ctx.get("adx", {}).get("val", 0))
            if isinstance(ctx.get("adx"), dict)
            else _safe_metric_to_int(ctx.get("adx", 0))
        )
        if ctx
        else 0,
        "z_score": ctx.get("z_score", 0.0) if ctx else 0.0,
        "vol_24h": ctx.get("vol_24h", 0.0) if ctx else 0.0,
        "trend_val": ctx.get("trend", "N/A") if ctx else "N/A",
        "funding_rate": ctx.get("funding_rate", 0.0) if ctx else 0.0,
        "tier": decision.get("tier", ctx.get("tier", "IRON")) if ctx else "IRON",
        "votos": votos or {},
        "pattern_type": pattern_type,
        "wr_hist": wr_hist,
        "ml_score": prob_ia * 100 if prob_ia > 0 else -1,
        "response_ms": response_ms,
    }

    _record_consensus_round(
        bot,
        symbol,
        decision,
        prob_ia * 100 if prob_ia > 0 else 0.0,
        display_verdict,
        ctx,
        votos,
        response_ms,
    )

    slock = getattr(bot, "scanner_lock", None)
    if slock:
        with slock:
            bot.scanner_history.insert(0, scanner_entry)
            if len(bot.scanner_history) > 100:
                bot.scanner_history = bot.scanner_history[:100]
    else:
        bot.scanner_history.insert(0, scanner_entry)
        if len(bot.scanner_history) > 100:
            bot.scanner_history = bot.scanner_history[:100]

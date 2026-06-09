import math

from config import Config


def _safe_metric_to_int(value, default=0):
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return int(default)
        return int(round(num))
    except Exception:
        return int(default)


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
            has_position = symbol in bot.active_trades
    else:
        has_position = symbol in bot.active_trades
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

from datetime import UTC

from config import Config
from core.cooldown_state import is_symbol_in_cooldown
from core.execution_telemetry import append_execution_event
from core.signals.cvd_filter import apply_cvd_filter
from core.signals.mtf.filter import apply_mtf_filter
from core.signals.oi_filter import fetch_oi_delta, validate_signal_with_oi
from core.time_utils import utc_now, utc_now_iso
from tools.strategy import Strategy


def _normalize_filter_reason(reason):
    text = str(reason or "").strip()
    if text.upper().startswith("VETO:"):
        text = text.split(":", 1)[1].strip()
    return text


def _snapshot_age_seconds(snapshot):
    try:
        from datetime import datetime

        ts_raw = snapshot.get("ts") if isinstance(snapshot, dict) else None
        if not ts_raw:
            return float("inf")
        parsed = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - parsed).total_seconds())
    except Exception:
        return float("inf")


def _get_markov_snapshot_mode(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("is_ready"):
        return "missing"
    age = _snapshot_age_seconds(snapshot)
    max_age = float(getattr(Config, "MARKOV_SNAPSHOT_MAX_AGE_SECONDS", 2 * 60 * 60))
    stale_age = float(getattr(Config, "MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 60 * 60))
    if age <= max_age:
        return "fresh"
    if age <= stale_age:
        return "stale_penalty_only"
    return "expired"


def _signal_markov_probability(snapshot, audit_signal):
    if audit_signal == "BUY":
        return float(
            snapshot.get("bullish_breakout_prob", snapshot.get("breakout_prob", 50.0)) or 0.0
        )
    if audit_signal == "SELL":
        return float(
            snapshot.get("bearish_reversal_prob", snapshot.get("breakout_prob", 50.0)) or 0.0
        )
    return float(snapshot.get("breakout_prob", 50.0) or 50.0)


def _record_markov_decision(
    bot,
    symbol,
    audit_signal,
    decision,
    btc_regime,
    snapshot_mode,
    markov_prob,
    regime_weight,
    previous_range_veto,
    filter_passed,
    filter_reason,
):
    stats = getattr(bot, "markov_decision_stats", None)
    if not isinstance(stats, dict):
        stats = {}
        bot.markov_decision_stats = stats
    stats[decision] = int(stats.get(decision, 0) or 0) + 1

    if decision == "missing_or_expired":
        return

    append_execution_event(
        bot,
        "MARKOV_REGIME_DECISION",
        {
            "symbol": symbol,
            "side": audit_signal,
            "decision": decision,
            "btc_regime": btc_regime,
            "snapshot_mode": snapshot_mode,
            "markov_prob": markov_prob,
            "regime_weight": float(regime_weight),
            "previous_range_veto": bool(previous_range_veto),
            "filter_passed": bool(filter_passed),
            "filter_reason": str(filter_reason),
        },
    )


def _apply_markov_regime_weight(
    bot,
    symbol,
    audit_signal,
    btc_regime,
    regime_weight,
    regime_reason,
    range_veto,
    filter_passed,
    filter_reason,
    ctx,
    prob_final=None,
):
    snapshot = ctx.get("hmm_data") if isinstance(ctx, dict) else None
    snapshot_mode = _get_markov_snapshot_mode(snapshot)
    previous_range_veto = bool(range_veto)
    if snapshot_mode in {"missing", "expired"}:
        if isinstance(ctx, dict):
            ctx["markov_snapshot_mode"] = snapshot_mode
        _record_markov_decision(
            bot,
            symbol,
            audit_signal,
            "missing_or_expired",
            btc_regime,
            snapshot_mode,
            None,
            regime_weight,
            previous_range_veto,
            filter_passed,
            filter_reason,
        )
        return regime_weight, regime_reason, range_veto, filter_passed, filter_reason, btc_regime

    hmm_state = str(snapshot.get("state") or btc_regime)
    markov_prob = _signal_markov_probability(snapshot, audit_signal)
    if isinstance(ctx, dict):
        ctx["btc_regime"] = hmm_state
        ctx["markov_prob"] = markov_prob
        ctx["markov_snapshot_mode"] = snapshot_mode

    allow_boost = snapshot_mode == "fresh"
    breakout_min = float(getattr(Config, "MARKOV_BREAKOUT_MIN", 75.0))
    dead_zone_max = float(getattr(Config, "MARKOV_DEAD_ZONE_MAX", 30.0))
    decision = None

    if hmm_state == "RANGE":
        range_veto = False
        if markov_prob >= breakout_min:
            regime_weight = float(getattr(Config, "MARKOV_RANGE_BREAKOUT_WEIGHT", 0.90))
            regime_reason = "RANGE_BREAKOUT_ANTICIPATION"
            decision = "range_breakout_allowed"
            if filter_passed:
                filter_reason = regime_reason
        elif markov_prob < dead_zone_max:
            # [HOTFIX v118.1] Dead zone: aplicar penalización standard en lugar de veto total
            # El mercado lateral estancado reduce probabilidades pero no bloquea señales válidas
            regime_weight = float(getattr(Config, "MARKOV_RANGE_STANDARD_WEIGHT", 0.75))
            regime_reason = "HMM_RANGE_PENALTY"
            decision = "range_dead_zone_penalty"
            if filter_passed:
                filter_reason = regime_reason
        else:
            regime_weight = float(getattr(Config, "MARKOV_RANGE_STANDARD_WEIGHT", 0.75))
            regime_reason = "RANGE_MARKOV_PENALTY"
            decision = "range_standard_penalty"
            if filter_passed:
                filter_reason = regime_reason
    elif hmm_state in {"BULL_STRONG", "BULL_TREND"} and audit_signal == "BUY" and allow_boost:
        regime_weight = float(getattr(Config, "MARKOV_BULL_STRONG_WEIGHT", 1.10))
        regime_reason = "MARKOV_BULL_ALIGNED"
        decision = "trend_boost"
    elif hmm_state in {"BEAR_STRONG", "BEAR_TREND"} and audit_signal == "SELL" and allow_boost:
        regime_weight = float(getattr(Config, "MARKOV_BEAR_STRONG_WEIGHT", 1.10))
        regime_reason = "MARKOV_BEAR_ALIGNED"
        decision = "trend_boost"

    if not allow_boost and regime_weight > 1.0:
        regime_weight = 1.0
        regime_reason = f"{regime_reason}_STALE_CAPPED"
        decision = "stale_capped"

    if decision:
        _record_markov_decision(
            bot,
            symbol,
            audit_signal,
            decision,
            hmm_state,
            snapshot_mode,
            markov_prob,
            regime_weight,
            previous_range_veto,
            filter_passed,
            filter_reason,
        )

    return regime_weight, regime_reason, range_veto, filter_passed, filter_reason, hmm_state


def _evaluate_bootstrap_heuristic(audit_signal, ctx):
    if audit_signal not in ["BUY", "SELL"] or not isinstance(ctx, dict):
        return {
            "heuristic_hits": [],
            "heuristic_confidence": 0.0,
            "bootstrap_ready_shadow": False,
            "bootstrap_ready_real": False,
        }

    rsi = float(ctx.get("rsi", 50.0) or 50.0)
    adx = float(ctx.get("adx", 0.0) or 0.0)
    vol_rel = float(ctx.get("vol_rel", 0.0) or 0.0)
    atr_pct = float(ctx.get("atr_pct", 0.0) or 0.0)
    close = float(ctx.get("close", 0.0) or 0.0)
    ema = float(ctx.get("ema", close) or close)

    hits = []
    if (audit_signal == "BUY" and close >= ema) or (audit_signal == "SELL" and close <= ema):
        hits.append("EMA_ALIGN")
    if adx >= 18.0:
        hits.append("ADX_OK")
    if (audit_signal == "BUY" and 52.0 <= rsi <= 68.0) or (
        audit_signal == "SELL" and 32.0 <= rsi <= 48.0
    ):
        hits.append("RSI_OK")
    if vol_rel >= 1.05:
        hits.append("VOL_OK")
    if 0.0 < atr_pct <= 0.05:
        hits.append("ATR_OK")

    hit_count = len(hits)
    return {
        "heuristic_hits": hits,
        "heuristic_confidence": min(90.0, 48.0 + (hit_count * 8.0)),
        "bootstrap_ready_shadow": hit_count >= 4,
        "bootstrap_ready_real": hit_count >= 5,
    }


def _resolve_btc_regime_adjustment(audit_signal, btc_regime):
    regime_weight = 1.0
    regime_reason = "N/A"
    range_veto = False

    if btc_regime == "BULL_TREND":
        if audit_signal == "BUY":
            regime_weight = 1.15
            regime_reason = "BULL_ALIGNED"
        else:
            regime_weight = 0.85
            regime_reason = "BULL_COUNTER"
    elif btc_regime == "BEAR_TREND":
        if audit_signal == "SELL":
            regime_weight = 1.15
            regime_reason = "BEAR_ALIGNED"
        else:
            counter_weight = float(getattr(Config, "BEAR_COUNTER_WEIGHT", 0.70))
            regime_weight = 0.85 * counter_weight
            regime_reason = "BEAR_COUNTER"
    elif btc_regime == "RANGE":
        regime_weight = max(0.0, float(getattr(Config, "HMM_RANGE_PENALTY", 0.5)))
        regime_reason = "RANGE_PENALTY"
        range_veto = bool(getattr(Config, "HMM_RANGE_VETO", False)) and audit_signal in [
            "BUY",
            "SELL",
        ]
        if range_veto:
            regime_weight = 0.0
            regime_reason = "RANGE_VETO"
    else:
        regime_reason = "RANGE_NEUTRAL"

    return regime_weight, regime_reason, range_veto


def _is_shadow_learning_runtime(bot) -> bool:
    execution_mode = str(getattr(bot, "execution_mode", "") or "").lower()
    backend = str(getattr(Config, "EXECUTION_BACKEND", "live") or "live").lower()
    paper_mode = bool(getattr(Config, "PAPER_MODE", True))
    return paper_mode and (execution_mode in {"shadow", "shadow_live"} or backend == "shadow_live")


def _apply_side_quality_parity_filter(
    audit_signal: str,
    ctx: dict,
    vol_rel: float,
    votos: dict | None = None,
) -> tuple[bool, str | None]:
    """Filtro de paridad: BUY y SELL exigen la misma calidad mínima.

    Reglas:
    - ADX >= SIDE_PARITY_MIN_ADX (25 por defecto)
    - vol_rel >= SIDE_PARITY_MIN_VOL_REL (0.30 por defecto)
    - En RANGE: RSI dentro de rangos (BUY <= 60, SELL >= 40)
    - Soporte de agentes: mínimo 2 de 3 agentes activos
    """
    if not bool(getattr(Config, "SIDE_PARITY_FILTER_ENABLED", True)):
        return True, None

    min_adx = float(getattr(Config, "SIDE_PARITY_MIN_ADX", 25.0))
    min_vol = float(getattr(Config, "SIDE_PARITY_MIN_VOL_REL", 0.30))
    range_buy_max_rsi = float(getattr(Config, "SIDE_PARITY_RANGE_BUY_MAX_RSI", 60.0))
    range_sell_min_rsi = float(getattr(Config, "SIDE_PARITY_RANGE_SELL_MIN_RSI", 40.0))
    min_agents = int(getattr(Config, "SIDE_PARITY_MIN_AGENT_SUPPORT", 2))

    rsi = float(ctx.get("rsi", 50.0))
    adx = float(ctx.get("adx", 20.0))
    trend = str(ctx.get("trend", "RANGO"))

    # 1. ADX mínimo (sin tendencia no hay señal de calidad)
    if adx < min_adx:
        return False, f"SIDE_PARITY: ADX {adx:.1f} < {min_adx:.0f}"

    # 2. Volumen mínimo
    if vol_rel < min_vol:
        return False, f"SIDE_PARITY: volumen {vol_rel:.2f} < {min_vol:.2f}"

    # 3. RSI bounds en RANGE
    if trend == "RANGO":
        if audit_signal == "BUY" and rsi > range_buy_max_rsi:
            return (
                False,
                f"SIDE_PARITY: BUY tarde en RANGE (RSI {rsi:.1f} > {range_buy_max_rsi:.0f})",
            )
        if audit_signal == "SELL" and rsi < range_sell_min_rsi:
            return (
                False,
                f"SIDE_PARITY: SELL tarde en RANGE (RSI {rsi:.1f} < {range_sell_min_rsi:.0f})",
            )

    # 4. Soporte de agentes (si hay votos disponibles)
    if votos and isinstance(votos, dict):
        # Un agente activo si vota fuera del rango neutral (45-55)
        active = sum(1 for v in votos.values() if abs(float(v) - 50.0) > 5.0)
        if active < min_agents:
            return False, f"SIDE_PARITY: solo {active}/{min_agents} agentes activos"

    return True, None


def _apply_entry_filters_and_adjust_prob(
    bot, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel, votos=None
):
    # Aplicar filtros de RSI, ADX y horario antes de evaluar.
    rsi_val = ctx.get("rsi", 50)
    adx_val = ctx.get("adx", 20)
    current_time = utc_now()
    volatility_val = ctx.get("atr_pct", 0)
    genes = None
    sl_modifier = 1.0

    try:
        with bot.db_lock:
            genes = bot.brain.get_genetic_params(symbol)
            stats = bot.brain.get_stats_by_trend()
        trend = str(ctx.get("trend", "RANGO"))
        if trend in stats and stats[trend].get("winrate", 50.0) < 45.0:
            sl_modifier = 0.80
    except Exception as error:
        bot.log(f"⚠️ No se pudo preparar contexto SL para {symbol}: {error}")
        genes = None
        sl_modifier = 1.0

    ctx["sl_modifier"] = sl_modifier
    ctx["sl_genes"] = genes or {}

    # [v118] Determinar prospecto de modo (Shadow/Real) para bypass de filtros
    prob_prospect = prob_final
    is_shadow_prospect = prob_prospect < (Config.REAL_CONFIDENCE_MIN * 100)

    (
        filter_passed,
        filter_reason,
        market_regime,
        adaptive_filters,
    ) = Strategy.check_entry_filters(
        rsi_val,
        adx_val,
        current_time,
        audit_signal,
        volatility_val,
        vol_rel,
        is_shadow=is_shadow_prospect,
        price=ctx.get("close", 0.0),
        atr=ctx.get("atr", 0.0),
        side=audit_signal,
        regime=ctx.get("trend", "RANGO"),
        modifier=sl_modifier,
        genes=genes,
    )

    btc_regime = bot._get_market_regime()
    regime_weight, regime_reason, range_veto = _resolve_btc_regime_adjustment(
        audit_signal, btc_regime
    )
    (
        regime_weight,
        regime_reason,
        range_veto,
        filter_passed,
        filter_reason,
        btc_regime,
    ) = _apply_markov_regime_weight(
        bot,
        symbol,
        audit_signal,
        btc_regime,
        regime_weight,
        regime_reason,
        range_veto,
        filter_passed,
        filter_reason,
        ctx,
        prob_final=prob_final,
    )
    ctx["btc_regime"] = btc_regime
    ctx["regime_weight"] = regime_weight
    ctx["regime_reason"] = regime_reason

    # [HURST] Ajuste por memoria de mercado en la probabilidad de señal
    hurst_value = ctx.get("hurst") if isinstance(ctx, dict) else None
    if filter_passed and hurst_value is not None and bool(getattr(Config, "HURST_ENABLED", True)):
        persistent_th = float(getattr(Config, "HURST_PERSISTENT_THRESHOLD", 0.55))
        antipersistent_th = float(getattr(Config, "HURST_ANTIPERSISTENT_THRESHOLD", 0.45))
        aligned_boost = float(getattr(Config, "HURST_ALIGNED_BOOST", 1.05))
        counter_penalty = float(getattr(Config, "HURST_COUNTER_PENALTY", 0.90))
        random_penalty = float(getattr(Config, "HURST_RANDOM_PENALTY", 0.95))

        if hurst_value >= persistent_th:
            if (audit_signal == "BUY" and btc_regime in ("BULL_TREND", "BULL_STRONG")) or (
                audit_signal == "SELL" and btc_regime in ("BEAR_TREND", "BEAR_STRONG")
            ):
                prob_final = min(100.0, prob_final * aligned_boost)
                ctx["hurst_boost"] = "PERSISTENT_ALIGNED"
                bot.log(f"📈 {symbol}: Hurst persistente + régimen alineado → x{aligned_boost:.2f}")
            else:
                prob_final = max(0.0, prob_final * counter_penalty)
                ctx["hurst_boost"] = "PERSISTENT_COUNTER"
                bot.log(f"⚠️ {symbol}: Hurst persistente + contra-régimen → x{counter_penalty:.2f}")
        elif hurst_value <= antipersistent_th:
            if btc_regime == "RANGE":
                prob_final = min(100.0, prob_final * aligned_boost)
                ctx["hurst_boost"] = "ANTIPERSISTENT_RANGE"
                bot.log(f"📈 {symbol}: Hurst antipersistente + RANGE → x{aligned_boost:.2f}")
        else:
            prob_final = max(0.0, prob_final * random_penalty)
            ctx["hurst_boost"] = "RANDOM_PENALTY"
            bot.log(f"⚠️ {symbol}: Hurst aleatorio (H≈0.5) → x{random_penalty:.2f}")

    allow_range_learning = range_veto and (
        bool(getattr(Config, "PAPER_MODE", True)) or _is_shadow_learning_runtime(bot)
    )
    if allow_range_learning:
        range_veto = False
        regime_weight = max(0.0, float(getattr(Config, "HMM_RANGE_PENALTY", 0.5)))
        regime_reason = "RANGE_PENALTY"
        ctx["regime_weight"] = regime_weight
        ctx["regime_reason"] = regime_reason
        bot.log(f"👻 {symbol}: RANGE permitido para aprendizaje BTC={btc_regime}")
        append_execution_event(
            bot,
            "RANGE_PENALTY",
            {
                "symbol": symbol,
                "side": audit_signal,
                "btc_regime": btc_regime,
                "regime_weight": regime_weight,
                "paper_mode": bool(getattr(Config, "PAPER_MODE", True)),
            },
        )

    if range_veto:
        filter_passed = False
        filter_reason = "RANGE REGIME VETO"
        bot.log(f"⛔ {symbol}: veto por régimen BTC={btc_regime} [{regime_reason}]")
        append_execution_event(
            bot,
            "RANGE_VETO",
            {
                "symbol": symbol,
                "side": audit_signal,
                "btc_regime": btc_regime,
                "paper_mode": bool(getattr(Config, "PAPER_MODE", True)),
            },
        )

    # [SIDE_PARITY] Calidad mínima simétrica para BUY y SELL
    if filter_passed:
        parity_ok, parity_reason = _apply_side_quality_parity_filter(
            audit_signal, ctx, vol_rel, votos
        )
        if not parity_ok:
            filter_passed = False
            filter_reason = parity_reason
            bot.log(f"⛔ {symbol}: {parity_reason}")

    # [BEAR_TREND PREVETO] Veto directo si hay alta probabilidad de reversión alcista
    if filter_passed and audit_signal == "BUY" and btc_regime == "BEAR_TREND":
        bearish_reversal_min = float(getattr(Config, "MARKOV_PREVETO_BEARISH_REVERSAL_MIN", 85.0))
        hmm_data = ctx.get("hmm_data") if isinstance(ctx, dict) else None
        if isinstance(hmm_data, dict) and hmm_data.get("is_ready"):
            reversal_prob = float(hmm_data.get("bearish_reversal_prob", 0.0) or 0.0)
            if reversal_prob >= bearish_reversal_min:
                filter_passed = False
                filter_reason = (
                    f"BEAR_REVERSAL_VETO ({reversal_prob:.1f}% >= {bearish_reversal_min:.1f}%)"
                )
                bot.log(
                    f"⛔ {symbol}: veto BUY en BEAR_TREND por reversión alcista "
                    f"prob={reversal_prob:.1f}% [{regime_reason}]"
                )

    if audit_signal == "BUY" and str(ctx.get("market_breadth_sentiment", "")).upper() == "FEAR":
        fear_threshold = float(getattr(Config, "MARKET_BREADTH_FEAR_THRESHOLD", 0.70) or 0.70)
        dump_ratio = float(ctx.get("market_breadth_dump_ratio", 0.0) or 0.0)
        if dump_ratio < fear_threshold:
            ctx["market_breadth_fear_ignored"] = True
        else:
            filter_passed = False
            filter_reason = f"MARKET_BREADTH_FEAR: FEAR ({dump_ratio * 100:.0f}% dump)"
            bot.log(
                f"⛔ {symbol}: veto LONG por Market Breadth FEAR ({dump_ratio * 100:.0f}% dump)"
            )

    # --- GLOBAL MARKET FILTERS (veto/boost por macro) ---
    if filter_passed:
        try:
            fear_greed = int(ctx.get("fear_greed_index", 50) or 50)
            btc_dom = float(ctx.get("btc_dominance", 0.0) or 0.0)
            fg_veto = int(getattr(Config, "GLOBAL_FEAR_VETO_THRESHOLD", 20))
            dom_boost = float(getattr(Config, "GLOBAL_BTC_DOM_BOOST_THRESHOLD", 65.0))
            FEAR_GREED_ENABLED = bool(getattr(Config, "GLOBAL_FEAR_GREED_FILTER_ENABLED", True))
            BTC_DOM_FILTER_ENABLED = bool(getattr(Config, "GLOBAL_BTC_DOM_FILTER_ENABLED", True))

            if FEAR_GREED_ENABLED and audit_signal == "BUY" and 0 < fear_greed < fg_veto:
                filter_passed = False
                filter_reason = f"FEAR_{fear_greed}_VETO: pánico extremo"
                bot.log(f"⛔ {symbol}: {filter_reason} (Fear & Greed={fear_greed})")

            if (
                filter_passed
                and BTC_DOM_FILTER_ENABLED
                and audit_signal == "SELL"
                and btc_dom > dom_boost
            ):
                prob_final = min(100.0, prob_final * 1.10)
                ctx["macro_boost_reason"] = f"BTC_DOM={btc_dom:.1f}%"
                bot.log(
                    f"📈 {symbol}: macro boost SELL (BTC dominance {btc_dom:.1f}% > {dom_boost:.0f}%)"
                )
        except Exception:
            None

    # [OI DELTA v118.3] Veto por senal falsa (short squeeze / long liquidation)
    # OI siempre se fetchea para el Context Vault; el filtro solo se aplica si está habilitado
    if filter_passed and audit_signal in ["BUY", "SELL"]:
        try:
            oi_delta_pct, oi_current = fetch_oi_delta(bot, symbol)
            if isinstance(ctx, dict):
                ctx["oi_delta_pct"] = oi_delta_pct
                ctx["oi_current"] = oi_current
            if oi_delta_pct is not None and bool(getattr(Config, "OI_FILTER_ENABLED", False)):
                delta_price_pct = 0.0
                oi_price_lookback = max(2, int(getattr(Config, "OI_PRICE_LOOKBACK_BARS", 2) or 2))
                if df_main is not None and not df_main.empty and len(df_main) >= oi_price_lookback:
                    price_now = float(df_main["close"].iloc[-1])
                    price_prev = float(df_main["close"].iloc[-oi_price_lookback])
                    if price_prev > 0:
                        delta_price_pct = (price_now - price_prev) / price_prev
                oi_verdict = validate_signal_with_oi(audit_signal, delta_price_pct, oi_delta_pct)
                if isinstance(ctx, dict):
                    ctx["oi_verdict"] = oi_verdict
                if oi_verdict == "VETO":
                    filter_passed = False
                    filter_reason = (
                        f"OI_DELTA_VETO: {audit_signal} falso "
                        f"(OI Δ={oi_delta_pct * 100:.2f}%, precio Δ={delta_price_pct * 100:.2f}%)"
                    )
                    bot.log(f"⛔ {symbol}: {filter_reason}")
                    append_execution_event(
                        bot,
                        "OI_DELTA_VETO",
                        {
                            "symbol": symbol,
                            "side": audit_signal,
                            "oi_delta_pct": oi_delta_pct,
                            "delta_price_pct": delta_price_pct,
                            "oi_current": oi_current,
                        },
                    )
                elif oi_verdict == "CONFIRMED":
                    bot.log(
                        f"✅ {symbol}: OI confirma {audit_signal} (OI Δ=+{oi_delta_pct * 100:.2f}%)"
                    )
        except Exception as oi_err:
            bot.log(f"⚠️ {symbol}: OI filter error (ignorado): {oi_err}")

    # [CVD / ORDER FLOW] Ajuste por agresores de mercado (sin veto duro inicial).
    if filter_passed and audit_signal in ["BUY", "SELL"]:
        prob_final, _cvd_passed, cvd_reason = apply_cvd_filter(
            bot,
            symbol,
            audit_signal,
            prob_final,
            ctx,
        )
        if isinstance(ctx, dict):
            ctx["cvd_reason"] = cvd_reason

    # [SHOCK MAP] Veto por falta de espacio operativo
    # Regla: si la distancia al próximo SHOCK < 1.0%, no se dispara.
    if filter_passed and audit_signal in ["BUY", "SELL"]:
        shock_dist_pct, shock_level = bot._get_shock_distance_pct(df_main, audit_signal)
        if ctx is not None:
            ctx["shock_dist_pct"] = shock_dist_pct
            ctx["shock_level"] = shock_level

        min_shock_dist = float(Config.SHOCK_MIN_DIST_PCT)
        if shock_dist_pct is not None and shock_dist_pct < min_shock_dist:
            # Breakout Hunter (pasivo): poner en acecho si IA es fuerte.
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)):
                added_watch = bot.breakout_agent.add_to_watchlist(
                    symbol=symbol,
                    side=audit_signal,
                    ia_prob=float(prob_final),
                    shock_level=float(shock_level) if shock_level is not None else 0.0,
                    trend=str(ctx.get("trend", "RANGO")),
                    metadata={
                        "source": "SHOCK_VETO",
                        "shock_dist_pct": shock_dist_pct,
                        "regime": bot._get_market_regime(),
                    },
                    min_ia_prob=float(
                        getattr(
                            Config,
                            "BREAKOUT_SHOCK_MIN_IA_PROB",
                            getattr(
                                Config,
                                "BREAKOUT_MIN_IA_PROB",
                                60.0,
                            ),
                        )
                    ),
                )
                if added_watch:
                    bot.log(
                        f"👁️ [ACECHO:SHOCK] {symbol} side={audit_signal} IA={prob_final:.1f}% "
                        f"shock={float(shock_level):.6f} dist={shock_dist_pct:.2f}%"
                    )
            filter_passed = False
            filter_reason = f"SHOCK DEMASIADO CERCA ({shock_dist_pct:.2f}% < {min_shock_dist:.2f}%)"

    # [MTF FILTER] 1h mantiene ownership; 15m/5m solo confirman o vetan entrada.
    if filter_passed and audit_signal in ["BUY", "SELL"]:
        prob_final, mtf_passed, mtf_reason = apply_mtf_filter(
            bot,
            symbol,
            audit_signal,
            prob_final,
            ctx,
            df_main,
        )
        if not mtf_passed:
            filter_passed = False
            filter_reason = mtf_reason
            bot.log(f"⛔ {symbol}: {filter_reason}")

    # Breakout Hunter (pasivo): evaluar ruptura con el df ya cargado (sin API extra)
    breakout_ready = False
    breakout_info = None
    if not range_veto and bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)):
        breakout_ready, breakout_info = bot.breakout_agent.evaluate_breakout(symbol, df_main)
        if breakout_ready and breakout_info is not None:
            bot.log(
                f"🚀 BREAKOUT_READY {symbol} side={breakout_info['side']} "
                f"close={breakout_info['breakout_close']:.6f} "
                f"shock={breakout_info['shock_level']:.6f} "
                f"vol={breakout_info['volume_now']:.2f}/{breakout_info['volume_avg20']:.2f}"
            )
            ctx["breakout_ready"] = True
            ctx["breakout_info"] = breakout_info

    # [v118] FILTRO BLACKLIST DE SÍMBOLOS (Mejorado)
    blacklist = getattr(Config, "SYMBOL_BLACKLIST", [])
    base_sym = symbol.split("/")[0]
    if symbol in blacklist or base_sym in [b.split("/")[0] for b in blacklist]:
        filter_passed = False
        filter_reason = f"Símbolo en blacklist ({symbol})"
        bot.log(f"⛔ {symbol} vetado: en blacklist")

    # Aplicar pesos de día/hora a la probabilidad IA
    day_weight = adaptive_filters.get("DAY_WEIGHT", 1.0)
    hour_weight = adaptive_filters.get("HOUR_WEIGHT", 1.0)

    # Info de pesos
    ctx["day_weight"] = day_weight
    ctx["hour_weight"] = hour_weight
    ctx["market_regime"] = market_regime

    # Loguear pesos
    if day_weight > 1.1 or hour_weight > 1.1:
        bot.log(f"⚡ {symbol}: Día x{day_weight:.2f}, Hora x{hour_weight:.2f} - MEJOR MOMENTO!")

    # Aplicar pesos de día/hora.
    day_weight = ctx.get("day_weight", 1.0)
    hour_weight = ctx.get("hour_weight", 1.0)
    combined_weight = (day_weight + hour_weight) / 2

    final_weight = combined_weight * regime_weight
    if regime_weight != 1.0:
        bot.log(f"📊 {symbol}: BTC={btc_regime} [{regime_reason}] x{regime_weight:.2f}")

    # [v118 paso B] BYPASS TEMPORAL PARA ELITE/GOLD
    # Conservamos la probabilidad original antes de aplicar pesos temporales
    original_prob = prob_final
    tier_current = ctx.get("tier", "IRON")

    if (
        tier_current in ["ELITE", "GOLD"]
        and original_prob >= 80.0
        and regime_reason not in ["RANGE_PENALTY", "RANGE_VETO"]
    ):
        if final_weight < 1.0:
            bot.log(
                f"⚡ [BYPASS] {symbol} ({tier_current}): Ignorando penalización temporal (x{final_weight:.2f})"
            )
            prob_final = original_prob  # Bypass total
    else:
        prob_final = min(original_prob * final_weight, 100)

    if final_weight != 1.0:
        bot.log(f"⚖️ {symbol}: Prob {original_prob:.1f} → {prob_final:.1f} (x{final_weight:.2f})")

    if bool(getattr(bot, "bootstrap_heuristic_mode", False)):
        bootstrap = _evaluate_bootstrap_heuristic(audit_signal, ctx)
        ctx.update(bootstrap)
        ctx["execution_mode"] = "BOOTSTRAP"
        prob_final = float(bootstrap["heuristic_confidence"])

    ctx["filter_passed"] = bool(filter_passed)
    ctx["filter_reason"] = filter_reason
    append_execution_event(
        bot,
        "FILTER_APPLIED",
        {
            "symbol": symbol,
            "side": audit_signal,
            "filter_passed": bool(filter_passed),
            "filter_reason": str(filter_reason),
            "prob_final": float(prob_final),
            "btc_regime": btc_regime,
            "regime_reason": regime_reason,
            "regime_weight": float(regime_weight),
            "markov_prob": ctx.get("markov_prob"),
            "markov_snapshot_mode": ctx.get("markov_snapshot_mode"),
            "oi_delta_pct": ctx.get("oi_delta_pct"),
            "oi_verdict": ctx.get("oi_verdict"),
            "cvd_imbalance": ctx.get("cvd_imbalance"),
            "cvd_direction": ctx.get("cvd_direction"),
            "cvd_weight": ctx.get("cvd_weight"),
        },
    )

    return prob_final, filter_passed, filter_reason, ctx


def _plan_execution_mode(
    bot,
    symbol,
    audit_signal,
    prob_final,
    audit_verdict,
    filter_passed,
    filter_reason,
    ctx,
):
    is_shadow_exec = True
    should_execute = False

    if filter_passed and "VETO" in str(audit_verdict).upper():
        filter_passed = False
        filter_reason = filter_reason or audit_verdict
        return should_execute, is_shadow_exec, audit_verdict, filter_passed, filter_reason

    REAL_THRESHOLD = Config.REAL_CONFIDENCE_MIN * 100

    # [BEAR_TREND] Elevar umbral para BUY en régimen bajista
    btc_regime_exec = bot._get_market_regime()
    if btc_regime_exec == "BEAR_TREND" and audit_signal == "BUY":
        bear_boost = float(getattr(Config, "BEAR_TREND_CONFIDENCE_BOOST", 10.0))
        REAL_THRESHOLD += bear_boost

    SHADOW_MIN_THRESHOLD = float(
        getattr(
            Config,
            "SHADOW_MODE_MIN",
            Config.SHADOW_PROB_MIN * 100,
        )
    )

    breakout_shadow_override = False
    if (
        bool(getattr(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", True))
        and audit_signal != "NEUTRAL"
        and not filter_passed
        and bool(ctx.get("breakout_ready", False))
        and "SHOCK DEMASIADO CERCA" in str(filter_reason)
        and prob_final
        >= max(SHADOW_MIN_THRESHOLD, float(getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0)))
    ):
        breakout_shadow_override = True
        is_shadow_exec = True
        should_execute = True
        bot.breakout_overrides_today += 1
        audit_verdict = f"🧪 BREAKOUT SHADOW READY (IA {prob_final:.1f}%)"
        bot.log(f"🧨 BREAKOUT OVERRIDE SHADOW: {symbol} [{audit_signal}] IA={prob_final:.1f}%")

    if bool(getattr(Config, "DIRECTIONAL_COHERENCE_FILTER", True)):
        sentiment_label = str(bot.current_sentiment[0])
        is_bull = "ALCISTA" in sentiment_label
        is_bear = "BAJISTA" in sentiment_label
        extreme_breakout_ok = breakout_shadow_override and prob_final >= float(
            getattr(Config, "BREAKOUT_EXTREME_IA_PROB", 75.0)
        )

        if audit_signal == "SELL" and is_bull and not extreme_breakout_ok:
            should_execute = False
            filter_passed = False
            filter_reason = "COHERENCIA: SELL bloqueado en régimen ALCISTA"
            audit_verdict = f"⛔ VETO: {_normalize_filter_reason(filter_reason)}"
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)) and bool(
                getattr(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True)
            ):
                shock_level_coh = ctx.get("shock_level") if ctx else None
                if shock_level_coh is not None:
                    added_watch = bot.breakout_agent.add_to_watchlist(
                        symbol=symbol,
                        side=audit_signal,
                        ia_prob=float(prob_final),
                        shock_level=float(shock_level_coh),
                        trend=str(ctx.get("trend", "RANGO")) if ctx else "RANGO",
                        metadata={
                            "source": "COHERENCE_VETO",
                            "shock_dist_pct": ctx.get("shock_dist_pct") if ctx else None,
                            "regime": bot._get_market_regime(),
                            "sentiment": sentiment_label,
                            "reason": filter_reason,
                        },
                        min_ia_prob=float(
                            getattr(
                                Config,
                                "BREAKOUT_COHERENCE_MIN_IA_PROB",
                                getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0),
                            )
                        ),
                    )
                    if added_watch:
                        bot.log(
                            f"👁️ [ACECHO:COHERENCIA] {symbol} side={audit_signal} IA={prob_final:.1f}% sentiment={sentiment_label}"
                        )
        elif audit_signal == "BUY" and is_bear and not extreme_breakout_ok:
            should_execute = False
            filter_passed = False
            filter_reason = "COHERENCIA: BUY bloqueado en régimen BAJISTA"
            audit_verdict = f"⛔ VETO: {_normalize_filter_reason(filter_reason)}"
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)) and bool(
                getattr(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True)
            ):
                shock_level_coh = ctx.get("shock_level") if ctx else None
                if shock_level_coh is not None:
                    added_watch = bot.breakout_agent.add_to_watchlist(
                        symbol=symbol,
                        side=audit_signal,
                        ia_prob=float(prob_final),
                        shock_level=float(shock_level_coh),
                        trend=str(ctx.get("trend", "RANGO")) if ctx else "RANGO",
                        metadata={
                            "source": "COHERENCE_VETO",
                            "shock_dist_pct": ctx.get("shock_dist_pct") if ctx else None,
                            "regime": bot._get_market_regime(),
                            "sentiment": sentiment_label,
                            "reason": filter_reason,
                        },
                        min_ia_prob=float(
                            getattr(
                                Config,
                                "BREAKOUT_COHERENCE_MIN_IA_PROB",
                                getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0),
                            )
                        ),
                    )
                    if added_watch:
                        bot.log(
                            f"👁️ [ACECHO:COHERENCIA] {symbol} side={audit_signal} IA={prob_final:.1f}% sentiment={sentiment_label}"
                        )

    if bool(getattr(bot, "bootstrap_heuristic_mode", False)):
        if audit_signal != "NEUTRAL" and filter_passed:
            hit_count = len(ctx.get("heuristic_hits", []))
            if bool(ctx.get("bootstrap_ready_real", False)):
                is_shadow_exec = False
                should_execute = True
                audit_verdict = f"🛠️ BOOTSTRAP REAL ({hit_count}/5 reglas)"
            elif bool(ctx.get("bootstrap_ready_shadow", False)):
                is_shadow_exec = True
                should_execute = True
                audit_verdict = f"🛠️ BOOTSTRAP SHADOW ({hit_count}/5 reglas)"
            else:
                should_execute = False
                audit_verdict = f"⏭️ BOOTSTRAP NO_FIRE ({hit_count}/5 reglas)"
        return should_execute, is_shadow_exec, audit_verdict, filter_passed, filter_reason

    if not breakout_shadow_override and audit_signal != "NEUTRAL" and filter_passed:
        if prob_final >= REAL_THRESHOLD:
            is_shadow_exec = False
            should_execute = True
            bot.log(f"🔥 DISPARO REAL: {symbol} confianza {prob_final:.1f}%")
        elif prob_final >= SHADOW_MIN_THRESHOLD:
            is_shadow_exec = True
            should_execute = True
            bot.log(f"🧪 DISPARO SHADOW: {symbol} confianza {prob_final:.1f}%")

    if not should_execute and audit_signal != "NEUTRAL" and prob_final >= SHADOW_MIN_THRESHOLD:
        if "SCOUT" in audit_verdict or "OK" in audit_verdict or "CONCESIÓN" in audit_verdict:
            is_shadow_exec = True
            should_execute = True
            bot.log(f"🔍 DEGRADACION A SHADOW: {symbol} (Veredicto: {audit_verdict})")

    return should_execute, is_shadow_exec, audit_verdict, filter_passed, filter_reason


def _resolve_audit_verdict_and_stats(
    bot,
    symbol,
    audit_signal,
    prob_final,
    ob_status,
    pnl_real_hoy,
    mode,
    ctx,
    filter_passed,
    filter_reason,
    ml_pure_prob,
    signal_stats,
):
    prob_ia_consensus = prob_final / 100.0
    audit_verdict = bot.get_audit_verdict(
        symbol,
        prob_ia_consensus,
        audit_signal,
        ob_status,
        pnl_real_hoy,
        bot.current_target,
        mode,
        ctx,
    )

    if not filter_passed:
        audit_verdict = f"⛔ VETO: {_normalize_filter_reason(filter_reason)}"
        if bool(ctx.get("breakout_ready", False)):
            audit_verdict += " | 👁️ BREAKOUT READY"
        bot.log(f"⛔ {symbol} vetado: {filter_reason}")
    elif prob_final > 95.0:
        bot.log(
            f"🚨 [KILL SWITCH] {symbol}: VETO por sobreconfianza ({prob_final:.1f}%). Posible overfitting."
        )
        audit_verdict = f"⛔ VETO: ML_CONF {prob_final:.1f}%"

    if ml_pure_prob >= 75.0 and "VETO" in audit_verdict:
        conflict_msg = (
            f"[A/B TEST CONFLICT] {utc_now_iso()} | {symbol} | "
            f"ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA OPERAR ({audit_signal}) PERO FUE VETADO POR: {audit_verdict}\n"
        )
        try:
            with open("conflict_ab.log", "a", encoding="utf-8") as handle:
                handle.write(conflict_msg)
        except Exception as error:
            bot.log(f"⚠️ No se pudo registrar conflicto A/B en {symbol}: {error}")
    elif ml_pure_prob < 50.0 and ("OK" in audit_verdict or "SHADOW" in audit_verdict):
        conflict_msg = (
            f"[A/B TEST CONFLICT] {utc_now_iso()} | {symbol} | "
            f"ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA ABORTAR PERO REGLAS APROBARON OPERAR ({audit_signal})\n"
        )
        try:
            with open("conflict_ab.log", "a", encoding="utf-8") as handle:
                handle.write(conflict_msg)
        except Exception as error:
            bot.log(f"⚠️ No se pudo registrar conflicto A/B en {symbol}: {error}")

    in_cd, remaining = is_symbol_in_cooldown(bot, symbol)
    if in_cd:
        audit_verdict = f"❄️ COOLDOWN ({remaining}m)"

    if (
        "VETO" in audit_verdict
        or "BLOQUEADO" in audit_verdict
        or "COOLDOWN" in audit_verdict
        or "RIESGO" in audit_verdict
    ):
        signal_stats["VETO"] += 1
    elif "SHADOW" in audit_verdict or "CONCESIÓN" in audit_verdict:
        signal_stats["SHADOW"] += 1
    elif "OK" in audit_verdict:
        signal_stats["REAL"] += 1

    return audit_verdict

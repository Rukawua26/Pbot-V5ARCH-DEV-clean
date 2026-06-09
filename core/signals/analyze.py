from datetime import UTC

from config import Config
from tools.strategy import Strategy


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


def _is_markov_snapshot_usable(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("is_ready"):
        return False
    max_age = float(getattr(Config, "MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 60 * 60))
    return _snapshot_age_seconds(snapshot) <= max_age


def _should_pre_veto_regime(bot, market_regime):
    if str(market_regime).upper() in {"CRASH", "PANIC", "BEAR_CRASH"}:
        return True, f"Crash regime ({market_regime})"
    if market_regime != "RANGE" or not bool(getattr(Config, "HMM_RANGE_VETO", False)):
        return False, None

    snapshot = getattr(bot, "hmm_markov_snapshot", None)
    if not _is_markov_snapshot_usable(snapshot):
        return True, f"Ranging market ({market_regime})"

    bearish_prob = float(snapshot.get("bearish_reversal_prob", 0.0) or 0.0)
    threshold = float(getattr(Config, "MARKOV_PREVETO_BEARISH_REVERSAL_MIN", 85.0))
    if bearish_prob >= threshold:
        return True, f"RANGE bearish reversal risk ({bearish_prob:.1f}% >= {threshold:.1f}%)"
    return False, None


def _is_shadow_learning_runtime(bot) -> bool:
    execution_mode = str(getattr(bot, "execution_mode", "") or "").lower()
    backend = str(getattr(Config, "EXECUTION_BACKEND", "live") or "live").lower()
    paper_mode = bool(getattr(Config, "PAPER_MODE", True))
    return paper_mode and (execution_mode in {"shadow", "shadow_live"} or backend == "shadow_live")


def _get_fast_coherence_veto_reason(bot, df_main):
    if not bool(getattr(Config, "DIRECTIONAL_COHERENCE_FILTER", True)):
        return None
    if df_main is None or df_main.empty or "close" not in df_main.columns:
        return None
    if "ema" not in df_main.columns:
        return None

    sentiment = str(getattr(bot, "current_sentiment", ("",))[0])
    is_bull = "ALCISTA" in sentiment
    is_bear = "BAJISTA" in sentiment
    if not is_bull and not is_bear:
        return None

    try:
        close_val = float(df_main["close"].iloc[-1])
        ema_val = float(df_main["ema"].iloc[-1])
    except Exception:
        return None

    tentative_signal = "BUY" if close_val > ema_val else "SELL"
    if tentative_signal == "SELL" and is_bull:
        return "COHERENCIA (FAST PATH): SELL bloqueado en regimen ALCISTA"
    if tentative_signal == "BUY" and is_bear:
        return "COHERENCIA (FAST PATH): BUY bloqueado en regimen BAJISTA"
    return None


def _analyze_symbol_candidate(bot, symbol_raw, symbol, df_main, df_4h, elapsed):
    try:
        if df_main is None or df_4h is None or df_main.empty:
            bot.update_radar(
                symbol,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⚪",
                "🚫 SIN DATOS",
                {"tier": "IRON"},
                response_ms=elapsed,
            )
            return None

        if bot.force_chaos_mode:
            df_main["atr"] = df_main["close"] * 0.06

        precio_actual = df_main["close"].iloc[-1]
        rango_promedio = (df_main["high"].tail(14) - df_main["low"].tail(14)).mean()
        atr_pct = (rango_promedio / precio_actual) * 100

        max_allowed_sl_pct = (Config.MAX_RISK_USD / Config.MIN_NOTIONAL_VALUE) * 100
        if (atr_pct * 1.5) > max_allowed_sl_pct:
            bot.update_radar(
                symbol,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⚪",
                f"⏭️ VOL EXTREMA ({atr_pct:.1f}%)",
                {"atr_pct": atr_pct / 100, "tier": "IRON"},
            )
            return None

        fast_veto_reason = _get_fast_coherence_veto_reason(bot, df_main)
        if fast_veto_reason:
            bot.log(f"⛔ FAST_VETO {symbol}: {fast_veto_reason}")
            bot.update_radar(
                symbol,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⚪",
                f"⛔ VETO: {fast_veto_reason}",
                {"tier": "IRON"},
                response_ms=elapsed,
            )
            return None

        market_regime = bot._get_market_regime()
        pre_veto, pre_veto_reason = _should_pre_veto_regime(bot, market_regime)
        if pre_veto:
            protects_real_capital = not bool(getattr(Config, "PAPER_MODE", True))
            if protects_real_capital:
                bot.log(f"⛔ REGIME VETO REAL {symbol}: {pre_veto_reason}")
                bot.update_radar(
                    symbol,
                    {"signal": "WAIT", "mode": "NONE"},
                    0.0,
                    "⚪",
                    f"⛔ VETO REAL: {pre_veto_reason}",
                    {"tier": "IRON", "regime": market_regime},
                    response_ms=elapsed,
                )
                return None
            if _is_shadow_learning_runtime(bot):
                bot.log(f"👻 SHADOW RANGE ALLOWED {symbol}: learning in {market_regime}")

        with bot.db_lock:
            dynamic_params = bot.brain.get_dynamic_settings(symbol)

        default_min = Config.SHADOW_MIN_PROBABILITY_RANGE / 10.0
        min_score = dynamic_params.get("min_score", default_min) if dynamic_params else default_min
        if bot.global_rag_impact > 10.0:
            min_score = max(min_score, 8.8)

        with bot.db_lock:
            res = Strategy.analyze(
                df_main.copy(),
                df_main.copy(),
                bot.brain,
                symbol=symbol,
                order_book=None,
                ghost_model=bot.ghost_model,
                scaler=bot.scaler,
                btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                min_score=min_score,
                funding_rate=0.0,
                df_4h=df_4h,
                market_regime=market_regime,
            )

        if res[3] >= 50.0:
            try:
                if bot.weight_tracker and bot.weight_tracker.should_block("market"):
                    order_book = None
                else:
                    order_book = bot.execution.fetch_order_book(symbol, limit=20)
                funding_rate = bot._get_cached_funding_rate(symbol)
            except Exception:
                order_book = None
                funding_rate = 0.0

            with bot.db_lock:
                res = Strategy.analyze(
                    df_main.copy(),
                    df_main.copy(),
                    bot.brain,
                    symbol=symbol,
                    order_book=order_book,
                    ghost_model=bot.ghost_model,
                    scaler=bot.scaler,
                    btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                    min_score=min_score,
                    funding_rate=funding_rate,
                    df_4h=df_4h,
                    market_regime=market_regime,
                )

        return res

    except KeyError as e_key:
        bot.log(f"⚠️ {symbol} descartado: Datos insuficientes para indicador clave ({e_key}).")
        bot.update_radar(
            symbol_raw,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            f"⚠️ KEY_ERR: {e_key}",
            {"tier": "IRON"},
        )
        return None
    except Exception as e_inner:
        bot.log(f"⚠️ Error análisis para {symbol}: {e_inner}")
        bot.update_radar(
            symbol_raw,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            f"❌ ERROR: {str(e_inner)[:15]}",
            {"tier": "IRON"},
        )
        return None

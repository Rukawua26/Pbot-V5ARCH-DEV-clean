import asyncio
import concurrent.futures
import time

import pandas as pd

from config import Config
from core.cooldown_state import is_symbol_in_cooldown
from core.execution_telemetry import append_execution_event
from core.market_breadth import calculate_market_breadth
from core.trade_keys import has_trade

_ANALYSIS_MISSING = object()


def _passes_cheap_pre_filters(
    bot, symbol_raw, symbol, res_data, controls=None, mutate_latency=True
):
    now = time.time()
    symbol_base = str(symbol).split("/")[0]
    controls = controls or {}
    if symbol_base in controls.get("blocked", set()):
        return False, "SYMBOL_BLOCKED", "⛔ SYMBOL BLOCKED", None

    if getattr(bot, "latency_quarantine", {}).get(symbol, 0.0) > now:
        return False, "LATENCY_QUARANTINED", "🔌 LATENCY QUARANTINE", None

    lock = getattr(bot, "lock", None)
    if lock:
        with lock:
            active = has_trade(getattr(bot, "active_trades", {}), symbol)
    else:
        active = has_trade(getattr(bot, "active_trades", {}), symbol)
    if active:
        return False, "SYMBOL_ALREADY_ACTIVE", "🔒 OPERACIÓN ACTIVA", None

    in_cd, remaining = is_symbol_in_cooldown(bot, symbol)
    if in_cd:
        return False, "COOLDOWN_ACTIVE", f"❄️ COOLDOWN ({remaining}m)", None

    if not res_data or not res_data.get("data"):
        elapsed = (res_data or {}).get("elapsed")
        return False, "DATA_INTEGRITY_FAIL", "⏱️ TIMEOUT", elapsed

    df_main, _df_4h = res_data["data"]
    elapsed = res_data.get("elapsed")
    if df_main is None or getattr(df_main, "empty", False):
        return False, "DATA_INTEGRITY_FAIL", "❌ NO_DATA", elapsed

    latency_veto_ms = int(getattr(Config, "LATENCY_VETO_MS", 4500))
    if elapsed is None or elapsed > latency_veto_ms or elapsed == -1:
        latency_quarantine_seconds = int(getattr(Config, "LATENCY_QUARANTINE_SECONDS", 300))
        if mutate_latency:
            bot.latency_quarantine[symbol] = now + latency_quarantine_seconds
        return False, "LATENCY_QUARANTINED", "🔌 LATENCIA", elapsed

    return True, "", "", elapsed


def _record_cheap_prefilter_veto(bot, symbol, reason, display, response_ms=None):
    bot.log(f"⏭️ CHEAP_PREFILTER_VETO {symbol}: {reason}")
    append_execution_event(
        bot,
        "CHEAP_PREFILTER_VETO",
        {"symbol": symbol, "reason": reason, "response_ms": response_ms},
    )
    bot.update_radar(
        symbol,
        {"signal": "WAIT", "mode": "NONE"},
        0.0,
        "⚪",
        display,
        {"tier": "IRON", "filter_reason": reason},
        response_ms=response_ms,
    )


def _precompute_signal_analysis(bot, top_triage, results):
    workers = int(getattr(Config, "SIGNAL_ANALYSIS_WORKERS", 1) or 1)
    if workers <= 1:
        return {}

    controls = {}
    load_controls = getattr(bot, "_load_runtime_symbol_controls", None)
    if callable(load_controls):
        controls = load_controls() or {}

    candidates = []
    for triage_entry in top_triage:
        symbol_raw = triage_entry["symbol"]
        symbol = symbol_raw.split(":")[0]
        res_data = results.get(symbol_raw)
        passed, _reason, _display, _response_ms = _passes_cheap_pre_filters(
            bot, symbol_raw, symbol, res_data, controls, mutate_latency=False
        )
        if not passed:
            continue
        df_main, df_4h = res_data["data"]
        elapsed = res_data["elapsed"]
        candidates.append((symbol_raw, symbol, df_main, df_4h, elapsed))

    if len(candidates) <= 1:
        return {}

    max_workers = max(1, min(workers, len(candidates)))
    analysis_by_symbol = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="signal-analysis",
    ) as executor:
        future_to_symbol = {
            executor.submit(
                bot._analyze_symbol_candidate,
                symbol_raw,
                symbol,
                df_main,
                df_4h,
                elapsed,
            ): symbol_raw
            for symbol_raw, symbol, df_main, df_4h, elapsed in candidates
        }
        for future in concurrent.futures.as_completed(future_to_symbol):
            symbol_raw = future_to_symbol[future]
            try:
                analysis_by_symbol[symbol_raw] = future.result()
            except Exception as error:
                bot.log(f"⚠️ Error análisis paralelo {symbol_raw}: {error}")
                analysis_by_symbol[symbol_raw] = None
    return analysis_by_symbol


def run_signal_scan_cycle(bot, top_triage, results, signal_stats, pnl_real_hoy):
    # FASE B: Análisis Secuencial (IA)
    breadth = calculate_market_breadth(
        results,
        fear_threshold=float(getattr(Config, "MARKET_BREADTH_FEAR_THRESHOLD", 0.70)),
        greed_threshold=float(getattr(Config, "MARKET_BREADTH_GREED_THRESHOLD", 0.70)),
    )
    bot.market_breadth = breadth.as_dict()
    if breadth.total_count > 0:
        bot.log(
            f"🌡️ MARKET_BREADTH sentiment={breadth.sentiment} "
            f"dump={breadth.dump_ratio * 100:.0f}% ({breadth.dump_count}/{breadth.total_count}) "
            f"pump={breadth.pump_ratio * 100:.0f}% ({breadth.pump_count}/{breadth.total_count})"
        )

    precomputed_analysis = _precompute_signal_analysis(bot, top_triage, results)
    controls = {}
    load_controls = getattr(bot, "_load_runtime_symbol_controls", None)
    if callable(load_controls):
        controls = load_controls() or {}

    for triage_entry in top_triage:
        symbol_raw = triage_entry["symbol"]
        symbol = symbol_raw.split(":")[0]

        res_data = results.get(symbol_raw)
        passed, reason, display, response_ms = _passes_cheap_pre_filters(
            bot, symbol_raw, symbol, res_data, controls
        )
        if not passed:
            _record_cheap_prefilter_veto(bot, symbol, reason, display, response_ms)
            continue

        df_main, df_4h = res_data["data"]
        elapsed = res_data["elapsed"]

        analysis = precomputed_analysis.get(symbol_raw, _ANALYSIS_MISSING)
        if analysis is _ANALYSIS_MISSING:
            analysis = bot._analyze_symbol_candidate(symbol_raw, symbol, df_main, df_4h, elapsed)
        if analysis is None:
            continue

        audit_signal, mode, price, prob_final, ind, votos = analysis
        if isinstance(ind, dict) and "spread" not in ind:
            ind["spread"] = triage_entry.get("spread", 0.0)
        append_execution_event(
            bot,
            "SIGNAL_ANALYZED",
            {
                "symbol": symbol,
                "side": audit_signal,
                "mode": mode,
                "price": float(price) if price is not None else None,
                "prob_final": float(prob_final),
                "market_regime": getattr(bot, "market_regime", None),
                "market_regime_source": getattr(bot, "market_regime_source", None),
            },
        )

        try:
            # [v118.5] Abortar si la estrategia detectó problemas de integridad
            if "error" in ind:
                # bot.log(f"⏭️ {symbol} descartado por estrategia: {ind['error']}")
                bot.update_radar(
                    symbol_raw,
                    {"signal": "WAIT", "mode": "NONE"},
                    0.0,
                    "⚪",
                    f"⏭️ {ind['error']}",
                    ind,
                )
                continue

            bot._update_signal_diagnostics(
                symbol,
                audit_signal,
                prob_final,
                mode,
                votos,
                ind,
                signal_stats,
            )

            (
                decision,
                ctx,
                ob_status,
                vol_rel,
            ) = bot._build_symbol_context(
                symbol_raw,
                symbol,
                df_main,
                price,
                ind,
                audit_signal,
            )

            prob_final, filter_passed, filter_reason, ctx = (
                bot._apply_entry_filters_and_adjust_prob(
                    symbol=symbol,
                    symbol_raw=symbol_raw,
                    df_main=df_main,
                    audit_signal=audit_signal,
                    prob_final=prob_final,
                    ctx=ctx,
                    vol_rel=vol_rel,
                    votos=votos,
                )
            )

            # --- Telemetría ML UI ---
            bot.last_ml_confidence = prob_final
            ml_pure_prob = 0.0 if bot.bootstrap_heuristic_mode else votos.get("G", 0.0)
            bot.last_ghost_weight = (
                0.0 if bot.bootstrap_heuristic_mode else getattr(bot, "ghost_weight_override", 35.0)
            )

            audit_verdict = bot._resolve_audit_verdict_and_stats(
                symbol=symbol,
                audit_signal=audit_signal,
                prob_final=prob_final,
                ob_status=ob_status,
                pnl_real_hoy=pnl_real_hoy,
                mode=mode,
                ctx=ctx,
                filter_passed=filter_passed,
                filter_reason=filter_reason,
                ml_pure_prob=ml_pure_prob,
                signal_stats=signal_stats,
            )

            if (
                not ind
                or ind.get("rsi", {}).get("val") == "--"
                or pd.isna(ind.get("rsi", {}).get("val"))
            ):
                bot.log(f"⚠️ SKIP {symbol}: RSI={ind.get('rsi', {}).get('val')} ind={bool(ind)}")
                bot.update_radar(
                    symbol_raw,
                    {"signal": "WAIT", "mode": "NONE"},
                    0.0,
                    "⚪",
                    "⏳ RSI N/A",
                    ind,
                )
                continue

            bot.log(
                f"🔎 {symbol}: signal={audit_signal} prob={prob_final} verdict={audit_verdict[:30] if audit_verdict else 'None'}"
            )

            # Actualizar radar unificado para evitar duplicados y errores de matching.
            bot.update_radar(
                symbol_raw,
                decision,
                prob_final / 100.0,
                ob_status,
                audit_verdict,
                ctx,
                votos,
                response_ms=elapsed,
            )

            is_shadow_exec = True
            should_execute = False

            (
                should_execute,
                is_shadow_exec,
                audit_verdict,
                filter_passed,
                filter_reason,
            ) = bot._plan_execution_mode(
                symbol=symbol,
                audit_signal=audit_signal,
                prob_final=prob_final,
                audit_verdict=audit_verdict,
                filter_passed=filter_passed,
                filter_reason=filter_reason,
                ctx=ctx,
            )

            if audit_signal in ["BUY", "SELL"] and not should_execute:
                payload = bot.data_service.sanitize_context(
                    {
                        **(ctx or {}),
                        "audit_verdict": audit_verdict,
                        "filter_passed": filter_passed,
                        "filter_reason": filter_reason,
                        "votos": votos,
                        "prob_final": prob_final,
                    }
                )
                if getattr(bot, "main_loop", None) is not None and bot.main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        asyncio.to_thread(
                            bot.brain.log_signal_alert,
                            symbol=symbol,
                            alert_type=audit_signal,
                            execution_mode=(
                                "BOOTSTRAP_NONE" if bot.bootstrap_heuristic_mode else "NONE"
                            ),
                            status="DISCARDED",
                            features=payload,
                        ),
                        bot.main_loop,
                    )
                else:
                    with bot.db_lock:
                        bot.brain.log_signal_alert(
                            symbol=symbol,
                            alert_type=audit_signal,
                            execution_mode=(
                                "BOOTSTRAP_NONE" if bot.bootstrap_heuristic_mode else "NONE"
                            ),
                            status="DISCARDED",
                            features=payload,
                        )

            # EJECUCIÓN FINAL + REFRESCO DE RADAR
            bot._execute_and_update_symbol(
                symbol_raw=symbol_raw,
                symbol=symbol,
                audit_signal=audit_signal,
                prob_final=prob_final,
                audit_verdict=audit_verdict,
                should_execute=should_execute,
                is_shadow_exec=is_shadow_exec,
                df_main=df_main,
                ctx=ctx,
                ob_status=ob_status,
                votos=votos,
                decision=decision,
                elapsed=elapsed,
            )

        except Exception as e:
            # Solo loggear errores críticos, no todos
            import traceback

            error_str = str(e)
            bot.log(f"❌ ERROR en {symbol}: {error_str} | {traceback.format_exc(limit=3)}")

            # Reportar el crash en el radar.
            slock = getattr(bot, "scanner_lock", None)
            if slock:
                with slock:
                    for item in bot.scanner_history:
                        if item["symbol"] == symbol:
                            item["result"] = f"❌ CRASH: {str(e)[:15]}"
                            break
            else:
                for item in bot.scanner_history:
                    if item["symbol"] == symbol:
                        item["result"] = f"❌ CRASH: {str(e)[:15]}"
                        break

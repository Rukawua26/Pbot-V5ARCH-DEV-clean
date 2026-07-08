import logging
import time
from datetime import datetime, timedelta
from typing import Any

from config import Config
from core.cooldown_state import set_symbol_cooldown
from core.execution_telemetry import append_execution_event
from core.kanban_sync import async_mover_tarjeta
from core.postmortem import label_exit_reason
from core.regime_tuning import record_trade as record_regime_trade
from core.runtime_metrics import append_runtime_metric
from core.shadow_validation import emit_shadow_trade_closed
from core.time_utils import parse_datetime_utc, utc_now
from core.trade_helpers import (
    _calculate_pnl_and_metrics,
    _module_available,
    _order_looks_filled,
    _release_simulated_margin,
)
from core.trade_helpers import (
    _exchange_position_is_flat as _helper_exchange_position_is_flat,
)
from core.trade_keys import find_trade_key, split_trade_key
from core.trade_state import TradeStatus
from tools.learning import shadow_logger

logger = logging.getLogger("SniperAI")

# MTF win-rate tracking accumulators
_MTF_TRADE_RESULTS: list[dict[str, Any]] = []


def _record_mtf_trade_outcome(trade: dict, pnl_percent: float, bot=None) -> None:
    """Track MTF-filtered trade outcome for periodic reporting."""
    global _MTF_TRADE_RESULTS
    snapshot = trade.get("market_snapshot", {})
    mtf_reason = snapshot.get("mtf_reason")
    if not mtf_reason:
        return
    _MTF_TRADE_RESULTS.append(
        {
            "mtf_reason": mtf_reason,
            "pnl_percent": pnl_percent,
            "is_win": pnl_percent > 0,
        }
    )
    if len(_MTF_TRADE_RESULTS) >= int(getattr(Config, "MTF_METRICS_WINDOW", 100)):
        _log_mtf_winrate_report(bot)


def _log_mtf_winrate_report(bot=None) -> None:
    """Log aggregated MTF win-rate report and reset accumulator."""
    global _MTF_TRADE_RESULTS
    if not _MTF_TRADE_RESULTS:
        return
    results = list(_MTF_TRADE_RESULTS)
    _MTF_TRADE_RESULTS = []

    total = len(results)
    wins = sum(1 for r in results if r["is_win"])
    win_rate = (wins / total * 100) if total > 0 else 0.0

    reasons = {}
    for r in results:
        reason = r["mtf_reason"]
        if reason not in reasons:
            reasons[reason] = {"total": 0, "wins": 0}
        reasons[reason]["total"] += 1
        if r["is_win"]:
            reasons[reason]["wins"] += 1

    per_reason = {}
    for reason, stats in reasons.items():
        per_reason[reason] = {
            "total": stats["total"],
            "wins": stats["wins"],
            "win_rate_pct": round((stats["wins"] / stats["total"]) * 100, 2)
            if stats["total"] > 0
            else 0.0,
        }

    append_execution_event(
        bot,
        "MTF_WINRATE_REPORT",
        {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate_pct": round(win_rate, 2),
            "per_reason": per_reason,
        },
    )


from tools.notifier import Priority, send_telegram_msg, send_telegram_photo


def _exchange_position_is_flat(bot, symbol: str, side: str | None = None) -> bool:
    if side is None:
        return _helper_exchange_position_is_flat(bot, symbol)
    return _helper_exchange_position_is_flat(bot, symbol, side)


def close_trade(
    bot,
    symbol: str,
    reason: str,
    exit_price: float,
    exit_confidence: float = 0.0,
    latency_context: dict[str, Any] | None = None,
    side: str | None = None,
    trade_key: str | None = None,
):
    key_symbol, key_side = split_trade_key(trade_key or symbol)
    real_symbol = key_symbol or symbol
    desired_side = side or key_side
    resolved_trade_key = trade_key or find_trade_key(bot.active_trades, real_symbol, desired_side)
    with bot.lock:
        trade = bot.active_trades.get(resolved_trade_key) if resolved_trade_key else None
        if trade and trade.get("closing_in_progress"):
            return
        if trade:
            trade["closing_in_progress"] = True
            trade["status"] = TradeStatus.CLOSING_INITIATED.value
    if not trade:
        return
    trade_key = str(trade.get("trade_key") or resolved_trade_key or real_symbol)
    symbol = str(trade.get("symbol") or real_symbol)
    side = str(trade.get("side") or desired_side or "")

    with bot.db_lock:
        bot.brain.save_active_trade_state(trade_key, trade)

    try:
        fees = 0
        close_failed = False
        order = None
        if not trade.get("is_shadow", False) and not Config.PAPER_MODE:
            try:
                bot.log(
                    f"\U0001f504 [CLOSING POSITION] {symbol} {trade['side']} (Reason: {reason})"
                )
                pre_api_ts = time.perf_counter()
                if "DEGRADED" in reason or "CONF_DEGRADED" in reason:
                    order = bot.execution.close_due_to_degradation(
                        symbol, trade["side"], trade["amount"]
                    )
                else:
                    order = bot.execution.close_position(symbol, trade["side"], trade["amount"])
                post_api_ts = time.perf_counter()

                if order:
                    bot.log(f"✅ CIERRE EXITOSO: {symbol} ID: {order.get('id', 'N/A')}")

                exit_state = str((order or {}).get("exit_state") or "").upper()
                if exit_state in {"STUCK", "FAILED", "OPEN_UNCONFIRMED"}:
                    raise RuntimeError(
                        f"Cierre no finalizado para {symbol}; exit_state={exit_state}"
                    )

                if not _exchange_position_is_flat(bot, symbol, trade.get("side")):
                    order_status = str((order or {}).get("status") or "UNKNOWN")
                    raise RuntimeError(
                        f"Cierre no confirmado en exchange para {symbol}; "
                        f"order_status={order_status}"
                    )

                if order and not _order_looks_filled(order):
                    bot.log(
                        f"⚠️ {symbol}: exposición remota plana aunque la orden reporta status={order.get('status', 'N/A')}"
                    )

                if latency_context and latency_context.get("signal_ts") is not None:
                    signal_to_api_ms = (pre_api_ts - float(latency_context["signal_ts"])) * 1000.0
                    api_ms = (post_api_ts - pre_api_ts) * 1000.0
                    total_ms = (post_api_ts - float(latency_context["signal_ts"])) * 1000.0
                    status = "OK" if total_ms < 450.0 else "SLOW"
                    trigger = latency_context.get("trigger", "UNKNOWN")
                    bot.log(
                        f"\u23f1\ufe0f SMART_EXIT_LATENCY {symbol} trigger={trigger} signal_to_api_ms={signal_to_api_ms:.1f} api_ms={api_ms:.1f} total_ms={total_ms:.1f} target_ms=450 status={status}"
                    )

            except Exception as e:
                bot.log(f"❌ ERROR CRÍTICO CERRANDO {symbol}: {e}")
                close_failed = False

                if (
                    "notional" in str(e).lower()
                    or "-4164" in str(e)
                    or "insufficient" in str(e).lower()
                ):
                    bot.log(f"⚠️ Error de min notional/dust detectado para {symbol}")
                    close_failed = True
                elif not isinstance(order, dict) or not _order_looks_filled(order):
                    bot.log(f"⚠️ Close order para {symbol} no confirmado como filled")
                    close_failed = True
                else:
                    try:
                        if not _exchange_position_is_flat(bot, symbol, trade.get("side")):
                            bot.log(f"⚠️ Posición remota no está plana tras close para {symbol}")
                            close_failed = True
                    except Exception as flat_error:
                        bot.log(
                            f"⚠️ No se pudo verificar posición plana tras close para {symbol}: {flat_error}"
                        )
                        close_failed = True

                if close_failed:
                    bot.is_paused = True
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    if trade_key in bot.active_trades:
                        bot.active_trades[trade_key]["status"] = TradeStatus.EXIT_STUCK.value
                        bot.active_trades[trade_key]["closing_in_progress"] = False
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(
                            trade_key, bot.active_trades.get(trade_key, {})
                        )
                    append_execution_event(
                        bot,
                        "REAL_CLOSE_FAILED_HALT",
                        {"symbol": symbol, "error": str(e), "closing_in_progress": False},
                    )
                    append_runtime_metric(
                        "halt",
                        {
                            "reason": "REAL_CLOSE_FAILED",
                            "symbol": symbol,
                            "error": str(e)[:180],
                        },
                    )
                    return

            time.sleep(1)
            try:
                my_trades = bot.execution.fetch_my_trades(symbol, limit=2)
                fees = sum(t["fee"]["cost"] for t in my_trades if t["fee"]["currency"] == "USDT")
            except Exception as error:
                bot.log(f"⚠️ No se pudo calcular fees reales de cierre para {symbol}: {error}")
        else:
            fees = (trade["entry"] * float(trade["amount"]) * Config.VIRTUAL_FEE) + (
                exit_price * float(trade["amount"]) * Config.VIRTUAL_FEE
            )
            if latency_context and latency_context.get("signal_ts") is not None:
                total_ms = (time.perf_counter() - float(latency_context["signal_ts"])) * 1000.0
                trigger = latency_context.get("trigger", "UNKNOWN")
                bot.log(
                    f"\u23f1\ufe0f SMART_EXIT_LATENCY {symbol} trigger={trigger} total_ms={total_ms:.1f} simulated=1 (PAPER/SHADOW)"
                )

        side = trade.get("side", "BUY")
        pnl_metrics = _calculate_pnl_and_metrics(trade, exit_price, fees, side)
        entry_price = trade["entry"]
        mae_price = trade.get("mae_price", entry_price)
        mfe_price = trade.get("mfe_price", entry_price)
        amt = pnl_metrics["amt"]
        pnl_neto_usd = pnl_metrics["pnl_neto_usd"]
        pnl_neto_percent = pnl_metrics["pnl_neto_percent"]
        mae_percent = pnl_metrics["mae_percent"]
        mfe_percent = pnl_metrics["mfe_percent"]
        if _release_simulated_margin(bot, trade, pnl_neto_usd):
            bot.log(
                f"💰 SIM_WALLET_RELEASE {symbol}: margin=${float(trade.get('margin_used') or 0.0):.2f} "
                f"pnl=${pnl_neto_usd:+.4f} balance=${float(getattr(bot, 'balance', 0.0) or 0.0):.2f} "
                f"available=${float(getattr(bot, 'available_balance', 0.0) or 0.0):.2f}"
            )

        pm_data = label_exit_reason(
            reason=reason,
            entry_price=entry_price,
            exit_price=exit_price,
            side=side,
            mae_percent=mae_percent,
            mfe_percent=mfe_percent,
            trade=trade,
            is_adopted=trade.get("adopted_orphan", False),
        )
        emit_shadow_trade_closed(
            trade,
            reason,
            exit_price,
            pnl_neto_usd,
            pnl_neto_percent,
            mae_percent,
            mfe_percent,
            pm_data.get("exit_reason", "UNKNOWN"),
        )

        bot.log(
            f"\U0001f50d DEBUG: Intentando guardar trade {symbol} | is_shadow={trade.get('is_shadow', False)}"
        )

        try:
            with bot.db_lock:
                trade_id = bot.brain.log_trade(
                    {
                        "symbol": symbol,
                        "side": trade["side"],
                        "entry": trade["entry"],
                        "exit": exit_price,
                        "pnl_usd": pnl_neto_usd,
                        "pnl_percent": pnl_neto_percent,
                        "reason": reason,
                        "is_shadow": trade.get("is_shadow", False),
                        "fees": fees,
                        "market_snapshot": trade.get("market_snapshot", {}),
                        "open_time": trade["open_time"].isoformat()
                        if isinstance(trade["open_time"], datetime)
                        else trade["open_time"],
                        "entry_ob": trade.get("entry_ob", "\u269a"),
                        "mae_percent": mae_percent,
                        "mfe_percent": mfe_percent,
                        "market_regime": trade.get("market_regime")
                        or (
                            bot._get_market_regime()
                            if callable(getattr(bot, "_get_market_regime", None))
                            else getattr(bot, "market_regime", "RANGE")
                        ),
                        "entry_confidence": trade.get("entry_confidence", 0.0),
                        "exit_confidence": exit_confidence,
                        "entry_shock_level": trade.get("entry_shock_level"),
                        "entry_atr": trade.get("entry_atr"),
                        "breakout_origin": trade.get("breakout_origin", False),
                        "entry_client_order_id": trade.get("entry_client_order_id"),
                        "sl_client_order_id": trade.get("sl_client_order_id"),
                        "tp_client_order_id": trade.get("tp_client_order_id"),
                        "entry_exchange_order_id": trade.get("entry_exchange_order_id"),
                        "sl_exchange_order_id": trade.get("sl_exchange_order_id"),
                        "tp_exchange_order_id": trade.get("tp_exchange_order_id"),
                        "exit_reason": pm_data.get("exit_reason", "UNKNOWN"),
                        "is_adopted": pm_data.get("is_adopted", 0),
                        "is_dirty": pm_data.get("is_dirty", 0),
                        "mae_at_sl": pm_data.get("mae_at_sl", 0.0),
                        "mfe_at_sl": pm_data.get("mfe_at_sl", 0.0),
                    }
                )
                bot.log(
                    f"\U0001f4be Trade guardado #{trade_id if trade_id else 'N/A'}: {symbol} | "
                    f"is_shadow={trade.get('is_shadow', False)} | PnL={pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
                )
                if trade_id:
                    try:
                        bot.brain.update_trade_context_result(
                            trade_id=trade_id,
                            pnl_percent=pnl_neto_percent,
                            exit_timestamp=utc_now().isoformat(),
                            is_winner=1 if pnl_neto_percent > 0 else 0,
                        )
                    except Exception as ctx_error:
                        bot.log(f"⚠️ Error actualizando trade context result: {ctx_error}")
                bot.brain.finalize_confidence_exit_audit(
                    trade.get("entry_client_order_id"),
                    trade_id or 0,
                    reason,
                    pnl_neto_usd,
                    pnl_neto_percent,
                )

                _record_mtf_trade_outcome(trade, pnl_neto_percent, bot=bot)

                votos = trade.get("market_snapshot", {}).get("votos", {})
                if votos:
                    shadow_logger.log(
                        {
                            "type": "TRADE_FEEDBACK",
                            "data": {
                                "symbol": symbol,
                                "pnl": pnl_neto_percent,
                                "votos": votos,
                            },
                        }
                    )
                    ctx_type = trade.get("market_snapshot", {}).get("context", "RANGE")
                    bot.brain.update_agent_reputation(
                        votos, pnl_neto_percent, context_type=ctx_type
                    )
        except Exception as e:
            bot.log(f"⚠️ Error guardando trade o reputación {symbol}: {e}")

        recent_trade = {
            "symbol": symbol,
            "side": trade.get("side", "?"),
            "entry": trade.get("entry", 0.0),
            "exit": exit_price,
            "pnl": pnl_neto_percent,
            "is_shadow": trade.get("is_shadow", False),
            "reason": reason,
            "closing_in_progress": False,
        }
        with bot.lock:
            recent = list(getattr(bot, "recent_closed_trades", []) or [])
            recent.insert(0, recent_trade)
            bot.recent_closed_trades = recent[:6]

        with bot.lock:
            if trade_key in bot.active_trades:
                del bot.active_trades[trade_key]

        with bot.db_lock:
            bot.brain.delete_active_trade_state(trade_key)

        # --- INTEGRACIÓN KANBAN ---
        # Solo movemos a Historial si el trade era REAL/PAPER (no SHADOW)
        kanban_id = trade.get("kanban_item_id")
        if kanban_id and not trade.get("is_shadow", True):
            async_mover_tarjeta(kanban_id, "Historial de Cierre")

        if bot.brain.evolve_genetics(symbol):
            bot.log(f"\U0001f9ec ADN MUTADO: {symbol} ha evolucionado sus parámetros SL/TP.")

        if trade.get("is_shadow", False):
            status, info = bot.brain.check_eureka_status(symbol)
            if status == "EUREKA":
                msg = (
                    f"\U0001f9e0 *¡EUREKA! NUEVO PATRÓN DETECTADO*\n"
                    f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                    f"\U0001f48e *Par:* {symbol}\n"
                    f"\U0001f4c8 *Tendencia:* {info['trend']}\n"
                    f"\U0001f4ca *Contexto:* {info['context']}\n"
                    f"\U0001f3af *Efectividad:* {info['wr']:.0f}% ({info['count']} pruebas)\n"
                    f"\U0001f4a1 *Lección:* Patrón validado con {info['context']}.\n"
                    f"\U0001f4dd *Acción:* Priorizando este patrón para entradas reales."
                )

                try:
                    df_snap = bot.data_service.fetch_and_update_data(symbol, Config.TIMEFRAME)
                    if df_snap is not None and not df_snap.empty:
                        if _module_available("tools.ai_mapper"):
                            from tools.ai_mapper import generate_strategy_snapshot

                            img = generate_strategy_snapshot(
                                symbol, df_snap.tail(100).reset_index(drop=True)
                            )
                            if img:
                                send_telegram_photo(msg, img)
                            else:
                                send_telegram_msg(msg)
                        else:
                            send_telegram_msg(msg)
                    else:
                        send_telegram_msg(msg)
                except Exception as e:
                    bot.log(f"⚠️ Error Visual Eureka: {e}")
                    send_telegram_msg(msg)

                bot.log(f"\U0001f9e0 EUREKA: {symbol} WR {info['wr']:.1f}%")

            elif status == "FAILURE":
                bot.brain.update_dynamic_settings(symbol, 9.0)
                send_telegram_msg(
                    f"\U0001f6e1\ufe0f *ESTUDIANTE ACTIVO: AUTO-CORRECCIÓN*\nHe detectado fallas repetidas en {symbol} ({info['wr']:.0f}% WR).\n\U0001f4c9 *Acción:* He vetado temporalmente este par."
                )
                bot.log(f"\U0001f6e1\ufe0f AUTO-VETO: {symbol} bloqueado por bajo rendimiento.")

        icono = "\U0001f47b SHADOW" if trade.get("is_shadow") else "\U0001f512 REAL"

        bot.log(
            f"{icono} CERRADO {symbol} ({reason}) | PnL: {pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
        )

        market_snap = trade.get("market_snapshot", {})
        entry_price = trade.get("entry", 0)
        entry_time = trade.get("open_time", "")
        entry_conf = float(trade.get("entry_confidence", 0.0) or 0.0)
        exit_conf = float(exit_confidence or 0.0)
        ia_delta = exit_conf - entry_conf

        shock_level = trade.get("entry_shock_level")
        shock_dist_pct = None
        try:
            if shock_level is not None and float(exit_price) > 0:
                shock_dist_pct = (
                    abs(float(shock_level) - float(exit_price)) / float(exit_price)
                ) * 100.0
        except Exception as exc:
            logger.warning(f"⚠️ shock_dist_pct calculation failed: {exc}")
            shock_dist_pct = None

        atr_val = float(
            trade.get("entry_atr")
            or market_snap.get("atr")
            or (market_snap.get("atr_pct", 0.0) * float(entry_price))
            or 0.0
        )
        drift_4h_est_pct = (
            ((atr_val / float(exit_price)) * 100.0 * 4.0)
            if float(exit_price) > 0 and atr_val > 0
            else 0.0
        )
        shock_dist_txt = f"{shock_dist_pct:.2f}%" if shock_dist_pct is not None else "N/A"
        duration = "N/A"
        if entry_time:
            try:
                if isinstance(entry_time, str):
                    entry_dt = parse_datetime_utc(entry_time)
                else:
                    entry_dt = parse_datetime_utc(entry_time)
                duration = utc_now() - entry_dt
                duration_mins = int(duration.total_seconds() / 60)
                duration = f"{duration_mins}m"
            except Exception as exc:
                logger.warning(f"⚠️ Duration parse failed: {exc}")
                duration = "N/A"

        emoji_pnl = "\U0001f7e2" if pnl_neto_percent > 0 else "\U0001f534"

        msg_telegram = (
            f"{icono} *CERRADO* {emoji_pnl}\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f539 *{symbol}*\n"
            f"\U0001f4c8 *PnL:* {pnl_neto_percent:+.2f}% | ${pnl_neto_usd:+.4f}\n"
            f"\U0001f4dd *Razón:* {reason}\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f194 Trade ID: #{trade_id if 'trade_id' in locals() and trade_id else 'N/A'}\n"
            f"\U0001f9e0 IA: {entry_conf:.1f}% \u2192 {exit_conf:.1f}% (\u0394 {ia_delta:+.1f}pp)\n"
            f"\U0001f3d4\ufe0f MFE: {mfe_percent:+.2f}%\n"
            f"\U0001f4cf Distancia SHOCK: {shock_dist_txt}\n"
            f"\U0001f30a Drift esperado 4h: {drift_4h_est_pct:.2f}%\n"
            f"\U0001f4b0 Entry: ${entry_price:.6f}\n"
            f"\U0001f4b8 Exit: ${exit_price:.6f}\n"
            f"\u23f1\ufe0f Duración: {duration}"
        )
        msg_priority = Priority.INFO
        reason_upper = str(reason or "").upper()
        if "CIRCUIT BREAKER" in reason_upper:
            msg_priority = Priority.CRITICAL
        elif "DEGRADED" in reason_upper or "BAILOUT" in reason_upper:
            msg_priority = Priority.ERROR
        elif pnl_neto_percent < 0:
            msg_priority = Priority.WARNING
        send_telegram_msg(msg_telegram, msg_priority)

        with bot.db_lock:
            stagnation = bot.brain.get_recent_exit_confidence_stagnation(limit=10)
        if stagnation and float(stagnation.get("stddev", 99.0)) < 1.0:
            bot.confidence_stagnation_lock_active = True
            bot.log(
                f"⚠️ CONFIDENCE_STAGNATION: last10 std={stagnation['stddev']:.3f} "
                f"mean={stagnation['mean']:.2f} range=[{stagnation['min']:.2f},{stagnation['max']:.2f}]"
            )
            send_telegram_msg(
                (
                    "⚠️ *CONFIDENCE STAGNATION*\n"
                    f"Últimos {stagnation['count']} cierres con exit_conf muy comprimida.\n"
                    f"StdDev: {stagnation['stddev']:.3f} | Media: {stagnation['mean']:.2f}\n"
                    f"Rango: {stagnation['min']:.2f} - {stagnation['max']:.2f}"
                ),
                Priority.WARNING,
            )
        bot._check_recent_mfe_health()

        now = utc_now()
        default_cd_until = now + timedelta(minutes=Config.TRADE_COOLDOWN_MINUTES)
        set_symbol_cooldown(bot, symbol, default_cd_until)
        bot.log(
            f"❄️ COOLDOWN UNIVERSAL: {symbol} bloqueado por {Config.TRADE_COOLDOWN_MINUTES}m tras cierre."
        )

        reason_txt = str(reason or "")
        smart_exit_abort = (
            reason_txt.startswith("DEGRADED_")
            or reason_txt.startswith("CONF_DEGRADED_")
            or "SHORT_THESIS_INVALIDATED" in reason_txt
            or "CONFIDENCE_FLOOR_VIOLATED" in reason_txt
            or "SUDDEN_CONFIDENCE_CRASH" in reason_txt
        )
        if smart_exit_abort:
            freeze_hours = float(getattr(Config, "SMART_EXIT_COOLDOWN_HOURS", 4))
            freeze_until = now + timedelta(hours=freeze_hours)
            current_until = now
            current_raw = (getattr(bot, "cooldown_pairs", {}) or {}).get(symbol)
            if current_raw is not None:
                try:
                    current_until = parse_datetime_utc(current_raw)
                except Exception as exc:
                    logger.warning(f"⚠️ Cooldown parse(1) failed: {exc}")
                    current_until = now
            if freeze_until > current_until:
                set_symbol_cooldown(bot, symbol, freeze_until)
            bot.log(
                f"\U0001f9ca SMART EXIT FREEZE: {symbol} bloqueado por {freeze_hours:.0f}h (razón={reason_txt[:60]})."
            )

        bot.risk_engine.record_trade_result(symbol, pnl_neto_percent)

        if bool(getattr(Config, "REGIME_TUNING_ENABLED", False)):
            trade_regime = trade.get("market_regime", "")
            if trade_regime:
                record_regime_trade(trade_regime, pnl_neto_percent)

        if pnl_neto_percent < 0 and not trade.get("is_shadow", False):
            anti_revenge_until = now + timedelta(hours=1)
            current_until = now
            current_raw = (getattr(bot, "cooldown_pairs", {}) or {}).get(symbol)
            if current_raw is not None:
                try:
                    current_until = parse_datetime_utc(current_raw)
                except Exception as exc:
                    logger.warning(f"⚠️ Cooldown parse(2) failed: {exc}")
                    current_until = now
            if anti_revenge_until > current_until:
                set_symbol_cooldown(bot, symbol, anti_revenge_until)
            bot.log(
                f"\U0001f6e1\ufe0f ANTI-REBOTE: {symbol} vetado por 1h adicional (pérdida en {'LONG' if trade['side'] == 'BUY' else 'SHORT'})."
            )

        if pnl_neto_percent < -15.0 and not trade.get("is_shadow"):
            bot.is_paused = True
            bot.pause_time = utc_now() + timedelta(hours=1)
            bot.log(
                f"\u2622\ufe0f CIRCUIT BREAKER: GAP masivo ({pnl_neto_percent:.2f}%). Pausando 1h."
            )
            send_telegram_msg(
                f"\u2622\ufe0f *CIRCUIT BREAKER:* GAP masivo en {symbol} ({pnl_neto_percent:.2f}%). Modo Real pausado 1h por seguridad."
            )

        bot._update_dynamic_risk()
    except Exception as e:
        error_str = str(e).upper()
        is_stuck_or_unconfirmed = any(
            x in error_str for x in ["STUCK", "OPEN_UNCONFIRMED", "EXIT_STATE="]
        )

        with bot.lock:
            current = bot.active_trades.get(trade_key)
            if current:
                current["closing_in_progress"] = False
                if is_stuck_or_unconfirmed and not (
                    trade.get("is_shadow", False) or Config.PAPER_MODE
                ):
                    current["status"] = TradeStatus.EXIT_STUCK.value
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    bot.log(
                        f"\U0001f6d1 CIERRE_STUCK {symbol}: estado EXIT_STUCK, HALT activado. "
                        f"Requiere intervención manual."
                    )
                    send_telegram_msg(
                        f"\U0001f6d1 *CIERRE_STUCK* {symbol} falló y activó HALT. "
                        f"Error: {str(e)[:100]}. Requiere intervención manual."
                    )
                    append_runtime_metric(
                        "halt",
                        {
                            "reason": "CIERRE_STUCK",
                            "symbol": symbol,
                            "error": str(e)[:180],
                        },
                    )
                else:
                    current["status"] = TradeStatus.OPEN.value
        with bot.db_lock:
            if current:
                bot.brain.save_active_trade_state(trade_key, current)
        bot.log(f"Error cerrando {symbol}: {e}")


def abort_partial_trade(bot, symbol: str, reason: str, exit_price: float):
    append_execution_event(
        bot,
        "PARTIAL_TRADE_ABORT_REQUESTED",
        {
            "symbol": symbol,
            "reason": reason,
            "exit_price": float(exit_price or 0.0),
        },
    )
    close_trade(
        bot,
        symbol=symbol,
        reason=reason,
        exit_price=exit_price,
        latency_context={"trigger": "GUARDIAN_PARTIAL_ABORT"},
    )

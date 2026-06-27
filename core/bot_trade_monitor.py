import time
from contextlib import nullcontext
from datetime import timedelta

from config import Config
from core.time_utils import parse_datetime_utc, utc_now
from core.trade_helpers import _calculate_trade_pnl
from core.trade_state import TradeStatus
from tools.strategy import Strategy


def _derive_dominant_killer(side, votes):
    if not isinstance(votes, dict) or not votes:
        return "UNKNOWN"
    normalized_side = str(side or "BUY").upper()
    if normalized_side == "SELL":
        return max(votes.items(), key=lambda item: float(item[1] or 0.0))[0]
    return min(votes.items(), key=lambda item: float(item[1] or 0.0))[0]


def _record_confidence_floor_event(
    bot,
    trade,
    symbol,
    prob_final,
    current_price,
    deg_reason,
    votos,
    defer_exit,
    defer_reason,
):
    entry_price = float(trade.get("entry") or 0.0)
    amount = float(trade.get("amount") or 0.0)
    if entry_price <= 0 or amount <= 0:
        return

    side = str(trade.get("side") or "BUY").upper()
    pnl_core = _calculate_trade_pnl(
        side=side,
        entry_price=entry_price,
        exit_price=current_price,
        amount=amount,
        leverage=trade.get("leverage", 1),
        fee_rate=Config.VIRTUAL_FEE,
        margin_used=trade.get("margin_used"),
        percent_on_margin=bool(trade.get("is_shadow", False) or trade.get("simulated_real", False)),
    )
    gross_usd = pnl_core["gross_usd"]
    gross_pct = pnl_core["gross_pct"]
    fee_floor_usd = pnl_core["fee_usd"]
    fee_floor_pct = (
        (fee_floor_usd / float(trade.get("margin_used") or pnl_core["notional_usd"])) * 100.0
        if float(trade.get("margin_used") or pnl_core["notional_usd"]) > 0
        else 0.0
    )
    entry_conf = float(trade.get("entry_confidence") or 0.0)
    confidence_drop_pct = (
        ((entry_conf - float(prob_final)) / entry_conf) * 100.0 if entry_conf > 0 else 0.0
    )
    defer_increment = 1 if defer_exit else 0

    db_lock = getattr(bot, "db_lock", None)
    with db_lock or nullcontext():
        audit_id = bot.brain.upsert_confidence_exit_audit(
            {
                "entry_client_order_id": trade.get("entry_client_order_id"),
                "symbol": symbol,
                "side": side,
                "is_shadow": bool(trade.get("is_shadow", False)),
                "entry_price": entry_price,
                "amount": amount,
                "entry_time": trade.get("open_time"),
                "entry_confidence": entry_conf,
                "floor_confidence": float(prob_final),
                "confidence_drop_pct": confidence_drop_pct,
                "floor_price": current_price,
                "gross_pnl_at_conf_drop_usd": gross_usd,
                "gross_pnl_at_conf_drop_pct": gross_pct,
                "fee_floor_usd": fee_floor_usd,
                "fee_floor_pct": fee_floor_pct,
                "fee_noise_zone": defer_exit,
                "guard_reason": defer_reason if defer_exit else "NO_DEFER",
                "trigger_reason": deg_reason,
                "votes": votos,
                "dominant_killer": _derive_dominant_killer(side, votos),
                "defer_increment": defer_increment,
            }
        )
    if audit_id:
        trade["confidence_exit_audit_id"] = audit_id


def monitor_open_trades(bot):
    """[FASE 3: BAILOUT] Auditoría continua de posiciones abiertas (Inteligencia Activa)."""
    with bot.lock:
        symbols = list(bot.active_trades.keys())

    if not symbols:
        return

    for trade_key in symbols:
        try:
            # [v118.6] IGNITION COOLDOWN: No bailouts en los primeros 15 minutos (micro-ruido inicial)
            # Permite que el trade respire y absorba el ruido de ejecución/spread.
            trade = bot.active_trades.get(trade_key)
            if not trade:
                continue
            symbol = str(trade.get("symbol") or trade_key).split("|")[0]
            if (
                trade.get("closing_in_progress")
                or trade.get("status") == TradeStatus.CLOSING_INITIATED.value
            ):
                continue

            open_time = parse_datetime_utc(trade.get("open_time") or utc_now())

            if utc_now() - open_time < timedelta(minutes=5):
                # bot.log(f"⏳ COOLDOWN ({symbol}): Ignorando bailout por juventud del trade.")
                continue
            # 1. Obtener datos frescos (Sello Institucional: solo 1H + 4H)
            df_main = bot.data_service.fetch_and_update_data(symbol, "1h")
            df_4h = bot.data_service.fetch_and_update_data(symbol, "4h")

            if df_main is None or df_main.empty:
                continue

            # 2. Re-evaluar con la IA (Consenso de los 14 Agentes)
            with bot.db_lock:
                res = Strategy.analyze(
                    df_main,
                    df_main,
                    bot.brain,
                    symbol=symbol,
                    ghost_model=bot.ghost_model,
                    scaler=bot.scaler,
                    btc_delta_tf=getattr(
                        bot,
                        "market_btc_change_tf",
                        0.0,
                    ),
                    df_4h=df_4h,
                    funding_rate=0.0,  # Simplificado para monitoreo
                )

            # res return: (signal, mode, exit_price, prob_final, indicators, votos)
            prob_final = res[3]
            indicators = res[4] if len(res) > 4 else {}
            votos = res[5] if len(res) > 5 else {}
            trade["current_confidence"] = prob_final
            trade["last_confidence_trace"] = {
                "prob_final": float(prob_final),
                "votes": dict(votos or {}),
                "regime": indicators.get("regime") if isinstance(indicators, dict) else None,
                "trend": indicators.get("trend") if isinstance(indicators, dict) else None,
                "bootstrap_mode": bool(getattr(bot, "bootstrap_heuristic_mode", False)),
            }
            duration = utc_now() - open_time
            elapsed_mins = duration.total_seconds() / 60

            if prob_final <= 30.0:
                bot.log(
                    f"🧠 CONF_AUDIT {symbol}: prob={prob_final:.1f}% "
                    f"MT={float((votos or {}).get('MT', 0.0)):.1f} "
                    f"SR={float((votos or {}).get('SR', 0.0)):.1f} "
                    f"G={float((votos or {}).get('G', 0.0)):.1f} "
                    f"bootstrap={int(bool(getattr(bot, 'bootstrap_heuristic_mode', False)))}"
                )

            # --- [V118] SMART EXIT: SALIDA POR DEGRADACIÓN ---
            if getattr(bot, "ghost_model", None) is None or bool(
                getattr(bot, "bootstrap_heuristic_mode", False)
            ):
                if prob_final <= 30.0:
                    bot.log(
                        f"🛑 CONF_EXIT_DISABLED {symbol}: Ghost ausente/bootstrap; "
                        f"prob={prob_final:.1f}% no se usa para cerrar."
                    )
                continue

            is_degraded, deg_reason = bot.risk_engine.check_signal_integrity(
                trade, prob_final, elapsed_mins
            )

            if is_degraded:
                entry_conf = trade.get("entry_confidence", 0)
                current_price = float(df_main["close"].iloc[-1])
                defer_exit, defer_reason = (
                    bot.risk_engine.should_defer_confidence_exit_for_fee_noise(
                        trade,
                        current_price,
                        elapsed_mins,
                        deg_reason,
                    )
                )
                _record_confidence_floor_event(
                    bot,
                    trade,
                    symbol,
                    prob_final,
                    current_price,
                    deg_reason,
                    votos,
                    defer_exit,
                    defer_reason,
                )
                if defer_exit:
                    bot.log(f"🪙 FEE_NOISE_GUARD ({symbol}): deferido smart-exit | {defer_reason}")
                    continue
                bot.log(
                    f"🚨 DEGRADED EXIT ({symbol}): {deg_reason} | EntryConf: {entry_conf:.1f} -> ExitConf: {prob_final:.1f}"
                )

                # Cierre inmediato ignorando TP/SL mediante ExecutionService
                bot.close_trade(
                    trade_key,
                    reason=f"DEGRADED_{deg_reason}",
                    exit_price=current_price,
                    exit_confidence=prob_final,
                    latency_context={
                        "trigger": "DEGRADED_EXIT",
                        "signal_ts": time.perf_counter(),
                        "entry_conf": entry_conf,
                        "exit_conf": prob_final,
                    },
                )
                continue

        except Exception as error:
            # Solo loguear errores importantes, no spam
            err_str = str(error)
            if "symbol" in err_str.lower() or "not found" in err_str.lower():
                bot.log(f"⚠️ Error monitoreando {symbol}: Símbolo no disponible en Binance")
            else:
                bot.log(f"⚠️ Error monitoreando {symbol}: {error}")

import time
from contextlib import nullcontext

from config import Config
from core.execution_telemetry import append_execution_event
from core.kanban_sync import async_actualizar_pnl, async_mover_tarjeta
from core.time_utils import monotonic_now, parse_datetime_utc, utc_now
from core.trade_helpers import _calculate_trade_pnl
from core.trade_state import TradeStatus
from tools.strategy import Strategy


def _fetch_prices_with_fallback(bot) -> dict:
    with bot.price_lock:
        price_map = bot.live_prices.copy()

    if not price_map:
        try:
            all_prices_raw = bot.execution.fetch_all_prices()
            price_map = {p["symbol"]: p["price"] for p in all_prices_raw}
        except Exception:
            price_map = {}

    return price_map


def _prioritize_symbols(snapshot: dict) -> list:
    real_syms = [s for s in snapshot.keys() if not snapshot[s].get("is_shadow")]
    shadow_syms = [s for s in snapshot.keys() if snapshot[s].get("is_shadow")]
    return real_syms + shadow_syms


def _sl_tightened(side: str, previous_sl: float, new_sl: float) -> bool:
    if previous_sl <= 0.0 or new_sl <= 0.0:
        return False
    if str(side).upper() == "BUY":
        return new_sl > previous_sl
    return new_sl < previous_sl


def _sync_tightened_hard_sl(bot, symbol: str, trade: dict, previous_sl: float) -> None:
    if Config.PAPER_MODE or trade.get("is_shadow", False):
        return
    new_sl = float(trade.get("sl") or 0.0)
    if not _sl_tightened(str(trade.get("side") or "BUY"), previous_sl, new_sl):
        return

    old_order_id = str(trade.get("sl_exchange_order_id") or "")
    if not old_order_id:
        return

    amend_count = int(trade.get("sl_amend_count") or 0) + 1
    base_coid = str(trade.get("sl_client_order_id") or "SL")
    new_coid = f"{base_coid[:28]}A{amend_count:03d}"
    amount = float(trade.get("amount") or 0.0)
    if amount <= 0.0:
        return

    new_order = bot.execution.place_hard_sl(
        symbol,
        str(trade.get("side") or "BUY"),
        amount,
        new_sl,
        client_order_id=new_coid,
    )
    if not new_order:
        bot.is_paused = True
        bot.integrity_lock_active = True
        setattr(bot, "halt_system_active", True)
        trade["status"] = "HARD_SL_AMEND_FAILED"
        with bot.db_lock:
            bot.brain.save_active_trade_state(symbol, trade)
        append_execution_event(
            bot,
            "HARD_SL_AMEND_FAILED_HALT",
            {"symbol": symbol, "old_sl": previous_sl, "new_sl": new_sl},
        )
        bot.log(f"🛑 HARD_SL_AMEND_FAILED {symbol}: SL local {previous_sl} -> {new_sl}")
        return

    cancel_order = getattr(bot.execution, "cancel_order", None)
    if callable(cancel_order):
        try:
            cancel_order(symbol, old_order_id)
        except Exception as error:
            bot.log(f"⚠️ No se pudo cancelar HARD SL anterior {symbol}/{old_order_id}: {error}")

    trade["sl_exchange_order_id"] = new_order.get("id")
    trade["sl_client_order_id"] = new_coid
    trade["hard_sl_price"] = new_sl
    trade["sl_amend_count"] = amend_count
    with bot.db_lock:
        bot.brain.save_active_trade_state(symbol, trade)
    append_execution_event(
        bot,
        "HARD_SL_AMENDED",
        {"symbol": symbol, "old_sl": previous_sl, "new_sl": new_sl, "old_order_id": old_order_id},
    )


def run_guardian_loop(bot):
    bot.log("🛡️ Guardián OK.")
    last_heavy = 0.0
    last_wallet_sync = 0.0
    while bot.is_running:
        loop_started = time.perf_counter()
        try:
            with bot.lock:
                snapshot = bot.active_trades.copy()

            # --- [SRE] VENTANA SEGURA DE RECARGA (Hot-Swap) ---
            # Si hay un modelo pendiente y NO tenemos trades abiertos, recargamos ahora.
            if bot.brain.pending_model_update and not snapshot:
                bot.log(
                    "🛡️ Guardián detecta ventana segura (0 trades). Iniciando Hot-Swap de modelo..."
                )
                bot.brain.reload_ghost_model(bot)

            # [v118] BAILOUT PRIORITARIO: Monitoreo de integridad de señales (Smart Exit)
            bot.monitor_open_trades()

            syms = list(snapshot.keys())
            if not syms:
                time.sleep(1)
                continue

            price_map = _fetch_prices_with_fallback(bot)
            sorted_syms = _prioritize_symbols(snapshot)

            for s in sorted_syms:
                try:
                    t = snapshot.get(s)
                    if not t:
                        continue
                    if (
                        t.get("closing_in_progress")
                        or t.get("status") == TradeStatus.CLOSING_INITIATED.value
                    ):
                        continue
                    if t.get("status") in {
                        TradeStatus.PARTIAL_FILL.value,
                        TradeStatus.PARTIAL_FILL_PENDING.value,
                    }:
                        current_conf = t.get("current_confidence", 50.0)
                        entry_conf = t.get("entry_confidence", 75.0)
                        is_shadow = t.get("is_shadow", False)
                        threshold = (
                            Config.SMART_EXIT_THRESHOLD_SHADOW
                            if is_shadow
                            else Config.SMART_EXIT_THRESHOLD_REAL
                        )
                        abort_needed, abort_reason = bot.risk_engine.should_abort_trade(
                            entry_conf, current_conf, threshold
                        )
                        if abort_needed:
                            append_execution_event(
                                bot,
                                "GUARDIAN_PARTIAL_ABORTED",
                                {
                                    "symbol": s,
                                    "status": t.get("status"),
                                    "entry_conf": float(entry_conf),
                                    "current_conf": float(current_conf),
                                    "reason": abort_reason,
                                },
                            )
                            bot.abort_partial_trade(
                                s,
                                f"PARTIAL_ABORT: {abort_reason}",
                                t.get("last_price", 0.0),
                            )
                            continue
                        append_execution_event(
                            bot,
                            "GUARDIAN_PARTIAL_OBSERVED",
                            {
                                "symbol": s,
                                "status": t.get("status"),
                                "amount": float(t.get("amount") or 0.0),
                                "remaining_amount": float(t.get("remaining_amount") or 0.0),
                            },
                        )
                        bot.log(
                            f"🧭 GUARDIAN PARTIAL {s}: observado {t.get('status')} y omitido para evitar desincronía"
                        )
                        continue

                    # Armamos el bailout de confianza solo cuando ya existe una lectura válida.
                    # Antes de eso usamos la confianza de entrada como baseline para no abortar con un fallback ficticio.
                    ot = t.get("open_time")
                    if isinstance(ot, str):
                        ot = parse_datetime_utc(ot)
                    elif ot is not None:
                        ot = parse_datetime_utc(ot)
                    else:
                        ot = utc_now()

                    current_conf = t.get("current_confidence", t.get("entry_confidence", 75.0))
                    entry_conf = t.get("entry_confidence", 75.0)
                    bailout_armed = (utc_now() - ot).total_seconds() >= (15 * 60)
                    abort_needed = False
                    abort_reason = "CONF_BAILOUT_COOLDOWN"
                    if bailout_armed:
                        abort_needed, abort_reason = bot.risk_engine.should_abort_trade(
                            entry_conf,
                            current_conf,
                            (
                                Config.SMART_EXIT_THRESHOLD_SHADOW
                                if t.get("is_shadow", False)
                                else Config.SMART_EXIT_THRESHOLD_REAL
                            ),
                        )

                    if abort_needed:
                        binance_symbol = s.replace("/", "")
                        abort_price = float(t.get("last_price") or 0.0)
                        if abort_price <= 0 and binance_symbol in price_map:
                            abort_price = float(price_map[binance_symbol])
                        if abort_price <= 0:
                            try:
                                abort_price = float(bot.execution.fetch_ticker(s)["last"])
                            except Exception:
                                abort_price = float(t.get("entry") or 0.0)
                        elapsed_mins = max(0.0, (utc_now() - ot).total_seconds() / 60.0)
                        defer_exit, defer_reason = (
                            bot.risk_engine.should_defer_confidence_exit_for_fee_noise(
                                t,
                                abort_price,
                                elapsed_mins,
                                abort_reason,
                            )
                        )
                        entry_price = float(t.get("entry") or 0.0)
                        amount = float(t.get("amount") or 0.0)
                        if entry_price > 0 and amount > 0:
                            gross_usd = (abort_price - entry_price) * amount
                            if str(t.get("side") or "BUY").upper() == "SELL":
                                gross_usd *= -1
                            notional = entry_price * amount
                            gross_pct = (gross_usd / notional) * 100.0 if notional > 0 else 0.0
                            fee_floor_usd = (entry_price * amount * Config.VIRTUAL_FEE) + (
                                abort_price * amount * Config.VIRTUAL_FEE
                            )
                            fee_floor_pct = (
                                (fee_floor_usd / notional) * 100.0 if notional > 0 else 0.0
                            )
                            votes = dict((t.get("last_confidence_trace") or {}).get("votes") or {})
                            side_key = str(t.get("side") or "BUY").upper()
                            dominant_killer = (
                                max(votes.items(), key=lambda item: float(item[1] or 0.0))[0]
                                if votes and side_key == "SELL"
                                else (
                                    min(votes.items(), key=lambda item: float(item[1] or 0.0))[0]
                                    if votes
                                    else "UNKNOWN"
                                )
                            )
                            db_lock = getattr(bot, "db_lock", None)
                            with db_lock or nullcontext():
                                audit_id = bot.brain.upsert_confidence_exit_audit(
                                    {
                                        "entry_client_order_id": t.get("entry_client_order_id"),
                                        "symbol": s,
                                        "side": side_key,
                                        "is_shadow": bool(t.get("is_shadow", False)),
                                        "entry_price": entry_price,
                                        "amount": amount,
                                        "entry_time": t.get("open_time"),
                                        "entry_confidence": float(t.get("entry_confidence") or 0.0),
                                        "floor_confidence": float(current_conf or 0.0),
                                        "confidence_drop_pct": (
                                            (
                                                (
                                                    float(t.get("entry_confidence") or 0.0)
                                                    - float(current_conf or 0.0)
                                                )
                                                / float(t.get("entry_confidence") or 1.0)
                                                * 100.0
                                            )
                                            if float(t.get("entry_confidence") or 0.0) > 0
                                            else 0.0
                                        ),
                                        "floor_price": abort_price,
                                        "gross_pnl_at_conf_drop_usd": gross_usd,
                                        "gross_pnl_at_conf_drop_pct": gross_pct,
                                        "fee_floor_usd": fee_floor_usd,
                                        "fee_floor_pct": fee_floor_pct,
                                        "fee_noise_zone": defer_exit,
                                        "guard_reason": defer_reason if defer_exit else "NO_DEFER",
                                        "trigger_reason": abort_reason,
                                        "votes": votes,
                                        "dominant_killer": dominant_killer,
                                        "defer_increment": 1 if defer_exit else 0,
                                    }
                                )
                            if audit_id:
                                t["confidence_exit_audit_id"] = audit_id
                        if defer_exit:
                            bot.log(f"🪙 FEE_NOISE_GUARD {s}: bailout diferido | {defer_reason}")
                            continue
                        bot.log(f"🚨 [v118-BAILOUT] {s}: Abortando por degradación de señal.")
                        bot._guardian_stats["bailout_count"] += 1
                        bot.close_trade(
                            s,
                            abort_reason,
                            abort_price,
                            latency_context={
                                "trigger": "BAILOUT_GUARDIAN",
                                "signal_ts": time.perf_counter(),
                                "entry_conf": entry_conf,
                                "exit_conf": current_conf,
                            },
                        )
                        continue

                    # Lógica de obtención de precio optimizada
                    binance_symbol = s.replace("/", "")
                    if binance_symbol in price_map:
                        curr = float(price_map[binance_symbol])
                    else:
                        # Fallback a fetch_ticker individual solo si el endpoint masivo falló o el par es nuevo
                        try:
                            curr = float(bot.execution.fetch_ticker(s)["last"])
                        except Exception as fetch_e:
                            bot.log(f"Guardian: No se pudo obtener precio para {s}: {fetch_e}")
                            continue  # Saltar al siguiente símbolo si no se puede obtener el precio
                    t["last_price"] = curr

                    # MAE/MFE Tracking (Maximum Adverse/Favorable Excursion)
                    side = t.get("side", "BUY")
                    if side == "BUY":
                        if curr < t.get("mae_price", float("inf")):
                            t["mae_price"] = curr
                        if curr > t.get("mfe_price", 0):
                            t["mfe_price"] = curr
                    else:
                        if curr > t.get("mae_price", 0):
                            t["mae_price"] = curr
                        if curr < t.get("mfe_price", float("inf")):
                            t["mfe_price"] = curr

                    # PnL Dinámico
                    pnl_core = _calculate_trade_pnl(
                        side=t.get("side", "BUY"),
                        entry_price=float(t.get("entry") or 0.0),
                        exit_price=curr,
                        amount=float(t.get("amount") or 0.0),
                        leverage=t.get("leverage", 1),
                        fee_rate=Config.VIRTUAL_FEE,
                        margin_used=t.get("margin_used"),
                        percent_on_margin=bool(
                            t.get("is_shadow", False) or t.get("simulated_real", False)
                        ),
                    )
                    t["pnl"] = pnl_core["net_pct"]

                    if t["pnl"] > t.get("peak_pnl", -999):
                        t["peak_pnl"] = t["pnl"]

                    # --- INTEGRACIÓN KANBAN ---
                    if t.get("kanban_item_id"):
                        if t.get("status") == "OPEN" and not t.get("kanban_moved_to_open"):
                            t["kanban_moved_to_open"] = True
                            async_mover_tarjeta(t["kanban_item_id"], "Posiciones Abiertas")
                        async_actualizar_pnl(t["kanban_item_id"], t["pnl"], curr)

                    # Exit Engine v118 (dinámico y persistente)
                    if bool(getattr(Config, "EXIT_ENGINE_V1_ENABLED", True)):
                        snap_ctx = t.get("market_snapshot", {}) or {}
                        current_atr = float(
                            t.get("entry_atr")
                            or snap_ctx.get("atr")
                            or snap_ctx.get("atr_pct", 0.0) * t.get("entry", 0.0)
                            or 0.0
                        )

                        previous_sl = float(t.get("sl") or 0.0)
                        exit_eval = bot.exit_engine.evaluate_exit(
                            trade=t,
                            current_price=curr,
                            current_atr=current_atr,
                            threshold_factor=(
                                Config.SMART_EXIT_THRESHOLD_SHADOW
                                if t.get("is_shadow", False)
                                else Config.SMART_EXIT_THRESHOLD_REAL
                            ),
                        )
                        _sync_tightened_hard_sl(bot, s, t, previous_sl)
                        now_ts = monotonic_now()
                        last_log_ts = float(bot._exit_eval_last_log.get(s, 0.0))
                        if now_ts - last_log_ts >= 120:
                            bot._exit_eval_last_log[s] = now_ts
                            bot.log(
                                f"🧭 EXIT_EVAL {s}: reason={exit_eval.get('reason')} pnl={t.get('pnl', 0.0):.2f}%"
                            )
                        if bool(exit_eval.get("should_exit", False)):
                            exit_reason = str(exit_eval.get("reason", "EXIT_ENGINE"))
                            bot.close_trade(s, exit_reason, curr)
                            continue

                    tp_price = float(t.get("tp") or 0.0)
                    if tp_price > 0.0:
                        if (t["side"] == "BUY" and curr >= tp_price) or (
                            t["side"] == "SELL" and curr <= tp_price
                        ):
                            t["tp_triggered"] = True
                            t["tp_trigger_price"] = curr
                            bot.close_trade(s, "TAKE_PROFIT", curr)
                            continue

                    # Fallback legacy de break-even (solo si Exit Engine v1 está desactivado).
                    if (
                        not bool(getattr(Config, "EXIT_ENGINE_V1_ENABLED", True))
                        and t["pnl"] >= Config.EARLY_BREAKEVEN_ACTIVATION_PNL
                        and not t.get("early_be_armed", False)
                    ):
                        be_fee_buffer = max(Config.VIRTUAL_FEE * 2, 0.0)
                        if t["side"] == "BUY":
                            be_sl = t["entry"] * (1.0 + be_fee_buffer)
                            should_tighten = be_sl > t.get("sl", 0)
                        else:
                            be_sl = t["entry"] * (1.0 - be_fee_buffer)
                            current_sl = t.get("sl", float("inf"))
                            should_tighten = be_sl < current_sl

                        if should_tighten:
                            previous_be_sl = float(t.get("sl") or 0.0)
                            t["sl"] = be_sl
                            _sync_tightened_hard_sl(bot, s, t, previous_be_sl)
                        t["early_be_armed"] = True
                        bot.log(
                            f"🛡️ EARLY BE {s}: PnL {t['pnl']:.2f}% >= {Config.EARLY_BREAKEVEN_ACTIVATION_PNL:.2f}% | "
                            f"SL ajustado a break-even con fees ({be_sl:.6f})."
                        )

                    # PARÁMETROS UNIFICADOS: Trailing basado en ATR dinámico
                    # [v119] Trailing activo cuando PnL >= 2.0x ATR (antes 0.8% fijo)
                    trailing_activation_atr = Config.ATR_TP1_MULTIPLIER  # 2.0x ATR
                    if trailing_activation_atr > 0:
                        entry_atr = float(t.get("entry_atr", 0) or 0)
                        entry_price = float(t.get("entry", 0) or 0)
                        if entry_atr > 0 and entry_price > 0:
                            trailing_pnl_pct = (
                                entry_atr * trailing_activation_atr / entry_price
                            ) * 100
                        else:
                            trailing_pnl_pct = Config.TRAILING_ACTIVATION_PNL
                    else:
                        trailing_pnl_pct = Config.TRAILING_ACTIVATION_PNL

                    if t["pnl"] >= trailing_pnl_pct:
                        t["trailing_active"] = True
                        if not t.get("trailing_activated_logged"):
                            bot.log(
                                f"🎯 TRAILING ARMED {s}: PnL {t['pnl']:.2f}% >= {trailing_pnl_pct:.2f}% (2.0x ATR)"
                            )
                            t["trailing_activated_logged"] = True

                    # Time Limit
                    # Time limit controlado por Config
                    # [SMART TIME LIMIT v118] No cerrar si va ganando (PnL > 0)
                    duration_mins = (utc_now() - ot).total_seconds() / 60
                    if duration_mins >= Config.MAX_TRADE_DURATION_MINUTES:
                        if t["pnl"] <= 0 or duration_mins >= Config.MAX_TRADE_DURATION_MINUTES * 2:
                            bot.close_trade(
                                s,
                                f"Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m{' (Force)' if t['pnl'] > 0 else ''}",
                                curr,
                            )
                            continue
                        else:
                            if not t.get("time_limit_warning"):
                                bot.log(
                                    f"⏳ {s}: Superado Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m pero PnL {t['pnl']:.2f}% > 0. Manteniendo..."
                                )
                                t["time_limit_warning"] = True

                    # --- NUEVO: DYNAMIC TRAILING (GHOST SENSITIVE) ---
                    # Si el trade va ganando (>0.5%) pero el Agente Ghost detecta peligro, apretamos a Break Even.
                    # [FIX] Solo activo para RF. LSTM requiere secuencia de 60 velas no disponible en bucle rápido.
                    if (
                        t["pnl"] > 0.5
                        and not t.get("ghost_checked", False)
                        and bot.ghost_model
                        and bot.ghost_model_type == "RF"
                    ):
                        try:
                            # Reconstruimos features rápidas (aproximación para velocidad)
                            snap = t.get("market_snapshot", {})
                            # Actualizamos precio actual en el snapshot para la IA
                            snap["close"] = curr
                            features = Strategy.prepare_ghost_features(
                                snap.get("rsi", 50),
                                snap.get("adx", 20),
                                snap.get("vol_rel", 0),
                            )

                            if hasattr(bot.ghost_model, "predict_proba"):
                                prob = bot.ghost_model.predict_proba(features)[0][1]

                                if (
                                    prob < 0.48
                                ):  # [v118-RELAXED] Umbral bajado de 0.55 a 0.48 para dar aire
                                    bot.log(
                                        f"👻 GHOST ALERT {s}: Probabilidad cayó a {prob:.2f} (Umbral 0.48). Apretando SL a Break Even."
                                    )
                                    previous_ghost_sl = float(t.get("sl") or 0.0)
                                    t["sl"] = t["entry"] * (
                                        1.001 if t["side"] == "BUY" else 0.999
                                    )  # Asegurar fees
                                    _sync_tightened_hard_sl(bot, s, t, previous_ghost_sl)
                                    t["ghost_checked"] = (
                                        True  # Solo chequear una vez para no saturar
                                    )
                        except (AttributeError, KeyError, IndexError) as error:
                            if not t.get("ghost_error_logged", False):
                                bot.log(
                                    f"⚠️ GHOST CHECK omitido en {s}: datos/modelo incompleto ({error})"
                                )
                                t["ghost_error_logged"] = True

                    # Shadow/Paper SL by price crossing. This is separate from REAL exchange HARD SL.
                    if (t["side"] == "BUY" and curr <= t["sl"]) or (
                        t["side"] == "SELL" and curr >= t["sl"]
                    ):
                        t["sl_triggered"] = True
                        t["sl_trigger_price"] = curr
                        t["sl_fill_price"] = curr
                        reason = "HARD_SL_SHADOW" if t.get("is_shadow", False) else "Dynamic SL"
                        bot.close_trade(s, reason, curr)
                        continue

                    # HARD STOP LOSS: Límite absoluto de pérdida
                    max_loss = (
                        Config.SHADOW_HARD_SL_PERCENT
                        if t.get("is_shadow", False)
                        else Config.REAL_HARD_SL_PERCENT
                    )

                    # PRE-HARD SL WARNING: evaluar trailing antes de llegar al Hard SL
                    pre_sl_warning = max_loss * 0.5  # -1.5% para REAL, -2.5% para SHADOW
                    if t["pnl"] <= pre_sl_warning and not t.get("pre_sl_warning_logged", False):
                        t["pre_sl_warning_logged"] = True
                        bot.log(
                            f"⚠️ PRE-SL WARNING {s}: PnL {t['pnl']:.2f}%接近 Hard SL ({max_loss}%) | "
                            f"forzando evaluación de trailing/BE"
                        )
                        # Forzar re-evaluación del exit engine
                        if bool(getattr(Config, "EXIT_ENGINE_V1_ENABLED", True)):
                            snap_ctx = t.get("market_snapshot", {}) or {}
                            current_atr = float(
                                t.get("entry_atr")
                                or snap_ctx.get("atr")
                                or snap_ctx.get("atr_pct", 0.0) * t.get("entry", 0.0)
                                or 0.0
                            )
                            exit_eval = bot.exit_engine.evaluate_exit(
                                trade=t,
                                current_price=curr,
                                current_atr=current_atr,
                            )
                            if bool(exit_eval.get("should_exit", False)):
                                exit_reason = str(exit_eval.get("reason", "PRE_SL_EXIT"))
                                bot.log(f"🚨 PRE-SL EXIT {s}: {exit_reason} | PnL {t['pnl']:.2f}%")
                                bot.close_trade(s, exit_reason, curr)
                                continue

                    if t["pnl"] <= max_loss:
                        bot.close_trade(s, f"Hard SL ({max_loss}%)", curr)
                        continue

                    # TP1: cerrar una fracción de la posición al primer objetivo.
                    if Config.TP1_ENABLED and not t.get("tp1_triggered", False):
                        if t["pnl"] >= Config.TP1_LEVEL:
                            # Cerrar la fracción configurada del tamaño.
                            close_amount = t.get("size_usd", 0) * (Config.TP1_PERCENT / 100)
                            if close_amount > 0:
                                bot.log(f"🎯 TP1 HIT: {s} - Cerrando 50% @ +{Config.TP1_LEVEL}%")
                                # Cerrar posición parcial
                                try:
                                    params = {"reduceOnly": True}
                                    if bot.is_hedge_mode:
                                        params["positionSide"] = (
                                            "LONG" if t["side"] == "BUY" else "SHORT"
                                        )
                                    bot.execution.create_reduce_only_market_order(
                                        s,
                                        "SELL" if t["side"] == "BUY" else "BUY",
                                        close_amount / curr,
                                        params=params,
                                    )
                                except Exception as e:
                                    bot.log(f"⚠️ Error TP1: {e}")

                                # [FIX v118] Marcar siempre como disparado para evitar bucles infinitos en errores de precisión/min_notional
                                t["tp1_triggered"] = True
                                t["size_usd"] = t.get("size_usd", 0) * (
                                    1 - Config.TP1_PERCENT / 100
                                )
                                t["amount"] = t.get("amount", 0) * (1 - Config.TP1_PERCENT / 100)
                            else:
                                t["tp1_triggered"] = True

                    # TP2: Cerrar resto a +2% con trailing
                    if (
                        Config.TP2_ENABLED
                        and t.get("tp1_triggered", False)
                        and not t.get("tp2_triggered", False)
                    ):
                        if t["pnl"] >= Config.TP2_LEVEL:
                            bot.log(f"🎯 TP2 HIT: {s} - Cerrando resto @ +{Config.TP2_LEVEL}%")
                            bot.close_trade(s, f"TP2 ({Config.TP2_LEVEL}%)", curr)
                            continue

                except Exception as e:
                    bot.log(f"Guardian error en {s}: {e}")

            # Sync wallet más reactivo para parciales (shadow/paper): 1s con parciales, 15s normal
            now_mono = monotonic_now()
            has_partial_pending = any(
                str((t or {}).get("status") or "")
                in {TradeStatus.PARTIAL_FILL.value, TradeStatus.PARTIAL_FILL_PENDING.value}
                for t in snapshot.values()
            )
            wallet_sync_interval = 1.0 if has_partial_pending else 15.0
            if now_mono - last_wallet_sync > wallet_sync_interval:
                bot.sync_wallet()
                last_wallet_sync = now_mono

            # 15s: Trailing pesado
            if now_mono - last_heavy > 15:
                # Usar snapshot ya capturado bajo lock (línea 41)
                # --- OPTIMIZACIÓN VIP: Primero REALES, luego SHADOW ---
                # Esto evita que el procesamiento de 30 trades shadow bloquee la protección de tu dinero real.
                sorted_trades = sorted(
                    list(snapshot.keys()),
                    key=lambda k: snapshot.get(k, {}).get("is_shadow", True),
                )

                for s in sorted_trades:
                    t = snapshot.get(s)
                    if not t or not t.get("trailing_active"):
                        continue

                    # === [MEJORADO v118] TRAILING STOP DINÁMICO ===
                    # Si TP1 ya fue ejecutado, usar trailing más agresivo
                    if Config.TRAIL_AFTER_TP1 and t.get("tp1_triggered", False):
                        # Trailing más agresivo después del TP1
                        trail_distance = Config.TRAIL_ENTRY_OFFSET  # 0.5%
                        if t["pnl"] >= Config.TP2_LEVEL:
                            trail_distance = 1.0  # [v118-OPTIMIZED] Subido de 0.3 a 1.0 para evitar asfixia post-TP1
                    else:
                        # Trailing normal basado en ATR
                        try:
                            df_main = bot.data_service.fetch_and_update_data(s, "1h")
                            if df_main is None or df_main.empty:
                                continue
                            atr = df_main.ta.atr(length=14).iloc[-1]
                            # FIX: Multiplicar por LEVERAGE para comparar peras con peras (PnL vs Distancia)
                            leverage_ref = 5  # Referencia estándar
                            dist = (
                                (atr / t["entry"])
                                * 100
                                * Config.TRAILING_ATR_MULTIPLIER
                                * leverage_ref
                            )
                            trail_distance = dist
                        except Exception:
                            trail_distance = Config.TRAILING_ACTIVATION_PNL

                    # Usamos Config.TRAILING_ACTIVATION_PNL para consistencia
                    if (
                        t["pnl"] <= (t.get("peak_pnl", 0) - trail_distance)
                        and t["pnl"] > Config.TRAILING_ACTIVATION_PNL
                    ):
                        bot.close_trade(s, "Trailing (ATR)", t["last_price"])
                    # NUEVO: Protección de breakeven para trades con buen profit
                    if t["pnl"] > Config.TRAILING_BREAKEVEN_PNL and t["pnl"] <= (
                        t.get("peak_pnl", 0) - Config.TRAILING_BREAKEVEN_PULLBACK
                    ):
                        bot.close_trade(
                            s,
                            "Trailing (Breakeven Protection)",
                            t["last_price"],
                        )
                last_heavy = now_mono

        except Exception as e:
            bot.log(f"Err Guardián: {e}")

        # MODULACIÓN DE FRECUENCIA v118: 0.1s para dominio < 500ms (trades activos), 2s tranquilo
        sleep_for = 0.1 if snapshot else 2.0
        work_s = max(time.perf_counter() - loop_started, 0.0)
        bot._guardian_stats["loops"] += 1
        bot._guardian_stats["work_s"] += work_s
        bot._guardian_stats["sleep_s"] += sleep_for
        time.sleep(sleep_for)

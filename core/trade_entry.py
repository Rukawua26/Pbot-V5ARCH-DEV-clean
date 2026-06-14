import time
from datetime import timedelta
from typing import Any

import core.trade_helpers as _trade_helpers
from config import Config
from core.cooldown_state import is_symbol_in_cooldown, set_symbol_cooldown
from core.execution_telemetry import append_execution_event
from core.kanban_sync import async_crear_tarjeta
from core.reconciliation import (
    allocate_signal_timestamp,
    generate_order_ids,
)
from core.regime_tuning import get_sl_multiplier, get_tp_multiplier
from core.risk.correlation_risk import compute_correlation_reduction
from core.risk_policy import evaluate_runtime_entry_decision, record_risk_decision
from core.symbol_utils import normalize_position_symbol  # noqa: F401 - compatibility patch target
from core.time_utils import utc_now, utc_now_iso
from core.trade_helpers import (
    _calculate_margin_used,
    _clamp_leverage_1_to_10,
    _fail_safe_close_when_sl_missing,
    _get_local_open_trade_counts,
    _release_simulated_margin,
    _reserve_simulated_margin,
    _safe_log_signal_alert,
    _safe_update_signal_alert_status,
    _sanitize_context,
    _validate_symbol_entry,
)
from core.trade_helpers import (
    _exchange_position_is_flat as _helper_exchange_position_is_flat,
)
from core.trade_helpers import (
    _validate_entry_preconditions as _helper_validate_entry_preconditions,
)
from core.trade_state import TradeStatus
from tools.learning import shadow_logger
from tools.notifier import Priority, send_telegram_msg


def _validate_entry_preconditions(bot, symbol: str, is_shadow: bool) -> str | None:
    _trade_helpers.shadow_logger = shadow_logger
    return _helper_validate_entry_preconditions(bot, symbol, is_shadow)


def _exchange_position_is_flat(bot, symbol: str) -> bool:
    return _helper_exchange_position_is_flat(bot, symbol)


def execute_order(
    bot,
    symbol: str,
    side: str,
    price: float,
    atr: float,
    is_shadow: bool = False,
    vol: float = 0,
    context: dict[str, Any] | None = None,
    ob_status: str = "\u269a",
    override_usd_size: float = 0.0,
) -> str:
    precheck = _validate_entry_preconditions(bot, symbol, is_shadow)
    if precheck:
        return precheck

    req_shadow = is_shadow
    degradation_reason = "UNKNOWN"
    signal_ts = allocate_signal_timestamp()

    deduper = getattr(bot, "intent_deduper", None)
    if deduper is not None and not deduper.check_and_register(symbol, side, signal_ts):
        bot.log(
            f"\U0001f501 DUPLICATE_INTENT {symbol} {side}: signal repetido en la ventana de dedup"
        )
        return "DUPLICATE_INTENT"

    instance_id = getattr(bot, "instance_uuid", "default")
    entry_client_order_id, sl_client_order_id, tp_client_order_id = generate_order_ids(
        symbol, side, signal_ts, instance_id
    )
    symbol_check = _validate_symbol_entry(bot, symbol, is_shadow)
    if symbol_check:
        return symbol_check

    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()

    execution_mode = "SHADOW" if is_shadow else ("PAPER" if Config.PAPER_MODE else "REAL")

    def _discard_pending_signal(reason: str) -> str:
        _safe_update_signal_alert_status(bot, entry_client_order_id, "DISCARDED")
        return reason

    if is_shadow:
        shadow_cd = int(getattr(Config, "SIGNAL_COOLDOWN_SHADOW_SECONDS", 60) or 60)
        last_signal = float(getattr(bot, "last_shadow_signal_ts", 0.0) or 0.0)
        if time.time() - last_signal < shadow_cd:
            return f"SHADOW_COOLDOWN ({int(shadow_cd - (time.time() - last_signal))}s)"

    risk_eng = getattr(bot, "risk_engine", None)
    if risk_eng is not None:
        dd_fn = getattr(risk_eng, "check_daily_drawdown", None)
        if dd_fn is not None:
            dd_safe, dd_reason = dd_fn(bot.balance)
            if not dd_safe:
                bot.log(f"🛑 DAILY DRAWDOWN: {symbol} {dd_reason}")
                return _discard_pending_signal(f"DAILY_DRAWDOWN_{dd_reason}")
        revenge_fn = getattr(risk_eng, "check_anti_revenge_blacklist", None)
        if revenge_fn is not None:
            revenge_safe, revenge_reason = revenge_fn(symbol)
            if not revenge_safe:
                bot.log(f"🚫 ANTI-REVENGE: {symbol} {revenge_reason}")
                return _discard_pending_signal(revenge_reason)

    _safe_log_signal_alert(
        bot,
        symbol=symbol,
        alert_type=side,
        execution_mode=execution_mode,
        status="PENDING",
        entry_client_order_id=entry_client_order_id,
        features=_sanitize_context(bot, context),
    )

    atr_pct = context.get("atr_pct", 0) if context else 0.02
    min_notional = Config.MIN_NOTIONAL_VALUE
    confidence_score = context.get("prob_final", 0.0) if context else 0.0
    current_leverage = _clamp_leverage_1_to_10(getattr(Config, "LEVERAGE", 10))

    max_notional_possible = bot.balance * current_leverage
    if max_notional_possible < min_notional:
        bot.log(
            f"❌ SALDO_INSUFICIENTE_PARA_MIN_NOTIONAL: Balance ${bot.balance:.2f} \u00d7 {current_leverage}x = ${max_notional_possible:.2f} < Min ${min_notional:.2f}"
        )
        return _discard_pending_signal("INSUFFICIENT_BALANCE_MIN_NOTIONAL")

    if (
        bool(getattr(Config, "REQUIRE_GHOST_MODEL_FOR_TRADING", True))
        and getattr(bot, "ghost_model", None) is None
    ):
        bot.log(
            f"\U0001f6d1 GHOST_MODEL_MISSING: bloqueando nueva entrada {symbol} hasta restaurar modelo IA."
        )
        append_execution_event(
            bot,
            "GHOST_MODEL_MISSING",
            {
                "symbol": symbol,
                "side": side,
                "execution_mode": execution_mode,
            },
        )
        if "REAL" in str(execution_mode).upper():
            send_telegram_msg(
                f"\U0001f6a8 *GHOST MODEL FALTANTE*\n"
                f"{symbol}: modelo ML no cargado — todas las señales REAL están bloqueadas.\n"
                f"Revise logs para determinar la causa.",
                Priority.WARNING,
            )
        return _discard_pending_signal("GHOST_MODEL_MISSING")

    atr_pct = context.get("atr_pct", 0) if context else 0.02
    if atr_pct * 100 > Config.NATR_THRESHOLD:
        bot.log(f"⚠️ VOLATILIDAD ALTA: {symbol} NATR {atr_pct * 100:.1f}%. Degradando a SHADOW.")
        is_shadow = True
        degradation_reason = "HIGH_VOLATILITY"
        if not req_shadow:
            send_telegram_msg(
                f"\U0001f6a8 *DEGRADACIÓN A SHADOW*\n"
                f"{symbol}: NATR {atr_pct * 100:.1f}% supera umbral {Config.NATR_THRESHOLD:.1f}%.\n"
                f"Señal REAL movida a SHADOW por volatilidad alta.",
                Priority.INFO,
            )

    trend = (context or {}).get("trend", "RANGO")
    spread = (context or {}).get("spread", 0.0)
    get_market_regime = getattr(bot, "_get_market_regime", None)
    entry_market_regime = str(
        (context or {}).get("btc_regime")
        or (get_market_regime() if callable(get_market_regime) else None)
        or getattr(bot, "market_regime", "RANGE")
    )

    try:
        ticker = bot.execution.fetch_ticker(symbol)
        current_price = float(ticker.get("last") or ticker.get("markPrice") or 0.0)
        if current_price > 0:
            price = current_price
    except Exception as error:
        bot.log(f"⚠️ No se pudo refrescar precio para {symbol}: {error}")
        if not getattr(Config, "PAPER_MODE", True) and not is_shadow:
            bot.log(f"🚫 ABORTO {symbol}: fallo refresh precio en modo REAL")
            return _discard_pending_signal(f"STALE_PRICE_ABORT ({error})")

    with bot.db_lock:
        genes = (context or {}).get("sl_genes")
        sl_modifier = float((context or {}).get("sl_modifier", 1.0) or 1.0)
        try:
            if genes is None:
                genes = bot.brain.get_genetic_params(symbol)
            if "sl_modifier" not in (context or {}):
                stats = bot.brain.get_stats_by_trend()
                if trend in stats and stats[trend].get("winrate", 50.0) < 45.0:
                    sl_modifier = 0.80
        except Exception as error:
            bot.log(f"⚠️ No se pudo ajustar SL por tendencia en {symbol}: {error}")

    regime_sl_mult = 1.0
    regime_tp_mult = 1.0
    if bool(getattr(Config, "REGIME_TUNING_ENABLED", False)):
        regime_sl_mult = get_sl_multiplier(bot, entry_market_regime)
        regime_tp_mult = get_tp_multiplier(bot, entry_market_regime)

    sl_val, tp_val, exit_mode = bot.risk_engine.get_exit_levels(
        entry_price=price,
        side=side,
        atr=atr,
        trend=trend,
        is_shadow=is_shadow,
        modifier=sl_modifier,
        genes=genes,
        spread=spread,
        fees=0.001,
        regime_sl_mult=regime_sl_mult,
        regime_tp_mult=regime_tp_mult,
        symbol=symbol,
    )
    bot.log(f"\U0001f9e9 Exit mode {symbol}: {exit_mode}")

    clean_snapshot = (context or {}).copy()
    clean_snapshot.setdefault("side", side)
    for heavy_key in ("df_1h", "df_4h", "df"):
        if heavy_key in clean_snapshot:
            del clean_snapshot[heavy_key]

    similarity_boost = 0.0
    similarity_verdict = "NEUTRAL"

    if clean_snapshot:
        try:
            similar = bot.brain.find_similar_contexts(clean_snapshot, limit=5)
            winners = []
            total_sim = len(similar or [])
            avg_pnl = 0.0
            if similar:
                winners = [s for s in similar if s.get("is_winner")]
                avg_pnl = sum(s.get("pnl_percent", 0.0) or 0.0 for s in similar) / total_sim
            n_winners = len(winners)
            n_losers = total_sim - n_winners

            if is_shadow and total_sim > 0:
                if n_winners >= 3 and avg_pnl > 1.0:
                    similarity_boost = min(5.0, avg_pnl * 0.5)
                    similarity_verdict = "BULLISH"
                elif n_losers >= n_winners and avg_pnl < -1.0:
                    similarity_boost = max(-10.0, avg_pnl * 2)
                    similarity_verdict = "BEARISH"
                else:
                    similarity_verdict = "MIXED"

            bot.log(
                f"🧠 SIMILARITY {symbol} {side}: "
                f"{n_winners}/{total_sim} winners "
                f"(avg PnL {avg_pnl:+.2f}%) → {similarity_verdict} ({similarity_boost:+.1f}%)"
            )

        except Exception as ctx_error:
            bot.log(f"⚠️ Error en similarity search: {ctx_error}")

    if similarity_verdict == "BULLISH":
        sizing_multiplier = 1.0 + (similarity_boost / 100.0)
    elif similarity_verdict == "BEARISH":
        sizing_multiplier = max(0.0, 1.0 + (similarity_boost / 100.0))
    else:
        sizing_multiplier = 1.0

    size_by_stop = getattr(bot.risk_engine, "calculate_position_size_by_stop", None)
    if callable(size_by_stop):
        amount, calculated_position_size = size_by_stop(
            balance=bot.balance,
            symbol=symbol,
            entry_price=price,
            stop_loss_price=sl_val,
            leverage=current_leverage,
            is_shadow=is_shadow,
            exchange=bot.execution.exchange,
        )
        sizing_label = "RISK SIZING"
    else:
        amount, calculated_position_size = bot.risk_engine.calculate_position_size(
            balance=bot.balance,
            symbol=symbol,
            price=price,
            leverage=current_leverage,
            context=context or {},
            is_shadow=is_shadow,
            exchange=bot.execution.exchange,
        )
        sizing_label = "KELLY SIZING"

    try:
        override_size = float(override_usd_size or 0.0)
    except (TypeError, ValueError):
        override_size = 0.0
    if override_size > 0.0:
        calculated_position_size = override_size
        amount = calculated_position_size / price if price > 0 else amount
        sizing_label = "OVERRIDE SIZING"

    if sizing_multiplier != 1.0 and calculated_position_size > 0:
        prev_notional = calculated_position_size
        calculated_position_size *= sizing_multiplier
        amount = calculated_position_size / price if price > 0 else amount
        bot.log(
            f"📐 SIMILARITY SIZING {symbol}: ${prev_notional:.2f} → ${calculated_position_size:.2f} "
            f"(×{sizing_multiplier:.3f}, {similarity_verdict})"
        )

    if not is_shadow and symbol_base in controls.get("reduced", set()):
        reduced_mult = max(0.1, min(bot._symbol_reduced_size_mult, 1.0))
        calculated_position_size *= reduced_mult
        amount = calculated_position_size / price if price > 0 else amount
        bot.log(
            f"\U0001f4d5 TACTICAL REDUCE {symbol}: size \u00d7 {reduced_mult:.2f} (decision matrix)"
        )

    open_symbols = list(bot.active_trades.keys()) if hasattr(bot, "active_trades") else []
    corr_mult, corr_details = compute_correlation_reduction(bot, symbol, open_symbols)
    if corr_mult < 1.0 and corr_details:
        calculated_position_size *= corr_mult
        amount = calculated_position_size / price if price > 0 else amount
        mean_corr = sum(d["correlation"] for d in corr_details) / len(corr_details)
        bot.log(
            f"\U0001f4d5 CORRELATION RISK {symbol}: size \u00d7 {corr_mult:.2f} "
            f"(\u03c1={mean_corr:.3f}, n={len(corr_details)})"
        )

    bot.log(
        f"\U0001f4ca [{sizing_label}] Balance: ${bot.balance:.2f} | Conf: {confidence_score:.1f}% | "
        f"Leverage: {current_leverage}x | SL: ${sl_val:.5f} | "
        f"Notional: ${calculated_position_size:.2f} | Amount: {amount}"
    )

    funding = (context or {}).get("funding_rate", 0)
    ob = bot.ws_manager.get_l2_state(symbol)
    btc_delta = getattr(bot, "market_btc_change_tf", 0)

    is_safe, reason, prob = bot.risk_engine.check_market_safety(
        (context.get("df_1h") if context else None),
        symbol,
        funding,
        side,
        ob,
        btc_delta,
    )

    if not is_safe:
        bot.log(
            f"\U0001f6e1\ufe0f RIESGO DETECTADO {symbol}: {reason} (Prob: {prob:.0f}%). Degradando a SHADOW."
        )
        is_shadow = True
        degradation_reason = reason
        if not req_shadow:
            send_telegram_msg(
                f"\U0001f6a8 *DEGRADACIÓN A SHADOW*\n"
                f"{symbol}: mercado no seguro ({reason}, prob: {prob:.0f}%).\n"
                f"Señal REAL movida a SHADOW por protección de capital.",
                Priority.INFO,
            )

    runtime_decision = evaluate_runtime_entry_decision(bot, symbol, is_shadow)
    if runtime_decision:
        record_risk_decision(bot, runtime_decision, symbol=symbol, is_shadow=is_shadow)
        bot.log(runtime_decision.log_message)
        if runtime_decision.reason == "CIRCUIT_BREAKER_PANIC":
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "CIRCUIT_BREAKER_HARD_PANIC",
                    bot.data_service.sanitize_context(context),
                )
        return _discard_pending_signal(runtime_decision.reason)

    global_cd = int(getattr(Config, "GLOBAL_ENTRY_COOLDOWN_SECONDS", 300) or 0)
    last_open_ts = float(getattr(bot, "last_entry_open_ts", 0.0) or 0.0)
    if global_cd > 0 and last_open_ts > 0:
        elapsed_global = time.time() - last_open_ts
        if elapsed_global < global_cd:
            remaining = int(global_cd - elapsed_global)
            bot.log(f"\u23f3 GLOBAL_COOLDOWN activo ({remaining}s restantes): {symbol} bloqueado")
            return _discard_pending_signal("GLOBAL_COOLDOWN")

    with bot.lock:
        if not is_shadow:
            base_coin = bot._get_base_coin(symbol)
            for active_symbol, active_trade in bot.active_trades.items():
                if (
                    not active_trade.get("is_shadow", False)
                    and bot._get_base_coin(active_symbol) == base_coin
                ):
                    bot.log(
                        f"⚠️ BLOQUEADO REAL {symbol}: Ya existe posición REAL abierta en {active_symbol}"
                    )
                    with bot.db_lock:
                        bot.brain.save_error_snapshot(
                            symbol,
                            "DUPLICATE_REAL",
                            bot.data_service.sanitize_context(context),
                        )
                    return _discard_pending_signal("DUPLICATE_REAL_COIN")

            current_sector = next(
                (
                    k
                    for k, v in Config.SECTORS.items()
                    if any(s.lower() in symbol.split("/")[0].lower() for s in v)
                ),
                "OTHE",
            )
            sector_count = sum(
                1
                for t in bot.active_trades.values()
                if t["sector"] == current_sector and not t.get("is_shadow", False)
            )
            if sector_count >= Config.MAX_SECTOR_EXPOSURE:
                return _discard_pending_signal(f"MAX_SECTOR_EXPOSURE ({current_sector})")

        if symbol in bot.active_trades:
            return _discard_pending_signal("ALREADY_ACTIVE")
        if not is_shadow:
            in_cd, _remaining = is_symbol_in_cooldown(bot, symbol)
            if in_cd:
                return _discard_pending_signal("COOLDOWN")

        actives = list(bot.active_trades.values())
        if Config.PAPER_MODE:
            num_real, num_shadow = _get_local_open_trade_counts(bot)
        else:
            num_real = sum(1 for t in actives if not t.get("is_shadow", False))
            num_shadow = sum(1 for t in actives if t.get("is_shadow", False))

        if not is_shadow:
            if num_real >= Config.MAX_OPEN_TRADES:
                bot.log(f"\u23f3 LÍMITE REAL ALCANZADO ({num_real}): {symbol} ignorado.")
                return _discard_pending_signal("MAX_REAL_TRADES")
            t_side = sum(1 for t in actives if t["side"] == side and not t.get("is_shadow", False))
            if t_side >= Config.MAX_DIRECTIONAL_TRADES:
                if num_shadow < Config.MAX_SHADOW_TRADES:
                    bot.log(
                        f"\U0001f504 LÍMITE DIRECCIONAL ({side}): {symbol} degradado a SHADOW para no perder oportunidad."
                    )
                    is_shadow = True
                    degradation_reason = "MAX_DIRECTIONAL_DEGRADED"
                else:
                    bot.log(
                        f"\u23f3 LÍMITE DIRECCIONAL ({side}) y SHADOW ({num_shadow}): {symbol} ignorado."
                    )
                    return _discard_pending_signal("MAX_DIRECTIONAL")
        elif num_shadow >= Config.MAX_SHADOW_TRADES:
            bot.log(f"\u23f3 LÍMITE SHADOW ALCANZADO ({num_shadow}): {symbol} ignorado.")
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "MAX_SHADOW",
                    bot.data_service.sanitize_context(context),
                )
            return _discard_pending_signal("MAX_SHADOW")

    pending_state = {
        "symbol": symbol,
        "side": side,
        "entry": price,
        "amount": amount,
        "is_shadow": is_shadow,
        "status": TradeStatus.PENDING_SEND.value,
        "signal_ts": signal_ts,
        "entry_client_order_id": entry_client_order_id,
        "sl_client_order_id": sl_client_order_id,
        "tp_client_order_id": tp_client_order_id,
        "entry_exchange_order_id": None,
        "sl_exchange_order_id": None,
        "tp_exchange_order_id": None,
        "open_time": utc_now_iso(),
        "intent_created_at_utc": utc_now_iso(),
        "intent_last_check_at_utc": None,
        "intent_check_attempts": 0,
    }
    append_execution_event(
        bot,
        "ORDER_INTENT_CREATED",
        {
            "symbol": symbol,
            "side": side,
            "is_shadow": bool(is_shadow),
            "entry_client_order_id": entry_client_order_id,
            "requested_price": float(price),
            "requested_amount": float(amount),
            "notional_usd": float(calculated_position_size),
        },
    )
    with bot.db_lock:
        persisted = bot.brain.save_active_trade_state(symbol, pending_state)
    append_execution_event(
        bot,
        "PENDING_SEND_PERSISTED",
        {
            "symbol": symbol,
            "entry_client_order_id": entry_client_order_id,
            "status": "PENDING_SEND",
        },
    )
    if not persisted:
        bot.log(
            f"❌ IDPOTENCY_GUARD {symbol}: no se pudo persistir intención PENDING_SEND antes de enviar orden"
        )
        return _discard_pending_signal("INTENT_PERSISTENCE_FAILED")

    def _drop_pending_intent():
        with bot.db_lock:
            bot.brain.delete_active_trade_state(symbol)

    try:
        regime_spreads = getattr(Config, "REGIME_SPREAD_THRESHOLDS", {})
        spread_veto_pct = regime_spreads.get(
            entry_market_regime,
            getattr(Config, "ENTRY_SPREAD_VETO_THRESHOLD", 0.0015),
        )
        try:
            fetch_book_ticker = getattr(bot.execution, "fetch_book_ticker", None)
            if callable(fetch_book_ticker):
                book_ticker = fetch_book_ticker(symbol)
            else:
                all_books = bot.execution.fetch_book_tickers() or []
                market_id = symbol.replace("/", "")
                book_ticker = next(
                    (
                        item
                        for item in all_books
                        if str(item.get("symbol") or "").upper() == market_id.upper()
                    ),
                    {},
                )
            bid = float(book_ticker.get("bidPrice", 0) or 0)
            ask = float(book_ticker.get("askPrice", 0) or 0)
            if bid > 0 and ask > 0:
                current_spread = (ask - bid) / ask
                if current_spread > spread_veto_pct:
                    bot.log(
                        f"\U0001f6ab VETO_SPREAD {symbol}: spread {current_spread * 100:.3f}% > {spread_veto_pct * 100:.3f}%"
                    )
                    append_execution_event(
                        bot,
                        "ENTRY_ABORTED_HIGH_SPREAD",
                        {
                            "symbol": symbol,
                            "spread_pct": current_spread * 100,
                            "threshold_pct": spread_veto_pct * 100,
                            "bid": bid,
                            "ask": ask,
                        },
                    )
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
                    _drop_pending_intent()
                    return f"HIGH_SPREAD_VETO ({current_spread * 100:.3f}%)"
        except Exception as spread_err:
            bot.log(f"⚠️ No se pudo verificar spread para {symbol}: {spread_err}")

    except Exception as error:
        bot.log(f"⚠️ No se pudo refrescar precio para {symbol}: {error}")
        if not getattr(Config, "PAPER_MODE", True):
            bot.log(f"🚫 ABORTO {symbol}: fallo refresh precio en modo REAL")
            _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
            _drop_pending_intent()
            return f"STALE_PRICE_ABORT ({error})"

    try:
        final_usd = calculated_position_size
        order = None
        sl_order = None
        simulated_margin_state = None
        if amount <= 0 or final_usd <= 0:
            bot.log(
                f"⚠️ ABORTO {symbol}: Tamaño inválido (amount={amount}, notional=${final_usd:.2f})"
            )
            _drop_pending_intent()
            return "SIZE_ERROR"

        min_notional_final = float(getattr(Config, "MIN_NOTIONAL_VALUE", 0.0) or 0.0)
        if final_usd < min_notional_final:
            bot.log(
                f"🚫 POST_REDUCTION_MIN_NOTIONAL {symbol}: ${final_usd:.2f} < ${min_notional_final:.2f}"
            )
            _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
            _drop_pending_intent()
            return "POST_REDUCTION_MIN_NOTIONAL"

        final_sl_pct = abs(float(price) - float(sl_val)) / float(price) * 100 if price > 0 else 0.0
        max_entry_sl_pct = float(getattr(Config, "MAX_ENTRY_SL_PCT", 0.0) or 0.0)
        if max_entry_sl_pct > 0 and final_sl_pct > max_entry_sl_pct:
            bot.log(f"🚫 FINAL_SL_TOO_WIDE {symbol}: {final_sl_pct:.2f}% > {max_entry_sl_pct:.2f}%")
            _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
            _drop_pending_intent()
            return "FINAL_SL_TOO_WIDE"

        final_risk_usd = float(amount) * abs(float(price) - float(sl_val))
        max_risk_usd = float(getattr(Config, "MAX_RISK_USD", 0.0) or 0.0)
        if not is_shadow and max_risk_usd > 0 and final_risk_usd > max_risk_usd:
            bot.log(f"🚫 FINAL_RISK_TOO_HIGH {symbol}: ${final_risk_usd:.2f} > ${max_risk_usd:.2f}")
            _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
            _drop_pending_intent()
            return "FINAL_RISK_TOO_HIGH"

        fees = 0.001
        spread_cost = (context or {}).get("spread", 0.0)
        tp_pct = abs(tp_val - price) / price * 100
        requested_amount = float(amount)
        filled_amount = float(amount)
        remaining_amount = 0.0
        avg_fill_price = float(price)
        min_tp = max(
            Config.MIN_TP_NET_PERCENT,
            (spread_cost + fees) * Config.MIN_TP_SPREAD_MULTIPLIER,
        )

        if tp_pct < min_tp:
            bot.log(f"\U0001f6ab TP INSUFICIENTE: {symbol} ({tp_pct:.2f}% < {min_tp:.2f}%)")
            if not is_shadow:
                _drop_pending_intent()
                return "TP_INSUFFICIENT"
            is_shadow = True

        margin_used = _calculate_margin_used(final_usd, current_leverage)
        simulated_margin_state = {
            "is_shadow": bool(is_shadow),
            "simulated_real": bool(Config.PAPER_MODE and not is_shadow),
            "margin_used": margin_used,
        }
        if is_shadow or (Config.PAPER_MODE and not is_shadow):
            reserve_ok, reserve_reason = _reserve_simulated_margin(bot, simulated_margin_state)
            if not reserve_ok:
                bot.log(f"🚫 SIM_MARGIN_BLOCK {symbol}: {reserve_reason}")
                _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
                _drop_pending_intent()
                return reserve_reason

        if not is_shadow and not Config.PAPER_MODE:
            bot.log(
                f"\U0001f680 [PRECISION ENTRY] {symbol} {side} ${final_usd:.2f} @ {price:.5f} (Lev: {current_leverage}x)"
            )
            leverage_result = bot.execution.set_leverage(current_leverage, symbol)
            if not leverage_result:
                bot.log(f"🚫 LEVERAGE_SETUP_FAILED {symbol}: abortando entrada REAL")
                append_execution_event(
                    bot,
                    "LEVERAGE_SETUP_FAILED",
                    {"symbol": symbol, "leverage": current_leverage},
                )
                _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")
                _drop_pending_intent()
                return "LEVERAGE_SETUP_FAILED"
            order_slippage = Config.MAX_SLIPPAGE * 100
            order = bot.execution.create_precision_order(
                symbol,
                side,
                amount,
                price,
                order_slippage,
                client_order_id=entry_client_order_id,
            )

            if order and order.get("status") in ["closed", "open", "filled"]:
                bot.log(f"✅ EJECUCIÓN EXITOSA: {symbol} ID: {order['id']}")
                requested_amount = float(amount)
                filled_amount = float(order.get("filled", requested_amount) or 0.0)
                remaining_amount = max(0.0, requested_amount - filled_amount)
                avg_fill_price = float(order.get("average") or order.get("price") or price)
                if filled_amount <= 0:
                    bot.log(f"❌ FALLO DE EJECUCIÓN: {symbol} sin fills confirmados")
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")
                    _drop_pending_intent()
                    return "EXECUTION_NO_FILL"

                append_execution_event(
                    bot,
                    "ENTRY_ORDER_ACK",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "exchange_order_id": order.get("id"),
                        "requested_amount": requested_amount,
                        "filled_amount": filled_amount,
                        "remaining_amount": remaining_amount,
                        "requested_price": float(price),
                        "avg_fill_price": avg_fill_price,
                        "slippage_simulated": avg_fill_price - float(price),
                        "status": str(order.get("status") or ""),
                    },
                )
                append_execution_event(
                    bot,
                    "ORDER_FILLED",
                    {
                        "symbol": symbol,
                        "side": side,
                        "is_shadow": False,
                        "entry_client_order_id": entry_client_order_id,
                        "exchange_order_id": order.get("id"),
                        "filled_amount": filled_amount,
                        "avg_fill_price": avg_fill_price,
                    },
                )
                if remaining_amount > 0.0:
                    append_execution_event(
                        bot,
                        "PARTIAL_FILL_DETECTED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "requested_amount": requested_amount,
                            "filled_amount": filled_amount,
                            "remaining_amount": remaining_amount,
                        },
                    )

                bot.log(f"\U0001f6e1\ufe0f Colocando HARD SL en Binance: {symbol} @ {sl_val}")
                sl_order = bot.execution.place_hard_sl(
                    symbol,
                    side,
                    filled_amount,
                    sl_val,
                    client_order_id=sl_client_order_id,
                )

                if not sl_order:
                    sl_error = str(getattr(bot.execution, "last_hard_sl_error", "") or "")
                    bot.log(
                        f"\u2622\ufe0f HARD_SL_ATTACH_FAILED {symbol}: entrada cerrada por fail-safe para evitar posición desnuda. error={sl_error[:180]}"
                    )
                    append_execution_event(
                        bot,
                        "ENTRY_ABORTED_NO_HARD_SL",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "sl_client_order_id": sl_client_order_id,
                            "sl_error": sl_error[:180],
                        },
                    )

                    closed = _fail_safe_close_when_sl_missing(bot, symbol, side, filled_amount)
                    if not closed:
                        bot.is_paused = True
                        bot.integrity_lock_active = True
                        setattr(bot, "halt_system_active", True)
                        pending_state["status"] = "EMERGENCY_CLOSE_PENDING"
                        pending_state["closing_in_progress"] = True
                        pending_state["last_hard_sl_error"] = sl_error[:180]
                        with bot.db_lock:
                            bot.brain.save_active_trade_state(symbol, pending_state)
                        with bot.lock:
                            bot.active_trades[symbol] = pending_state
                        append_execution_event(
                            bot,
                            "FAIL_SAFE_CLOSE_FAILED_HALT",
                            {
                                "symbol": symbol,
                                "entry_client_order_id": entry_client_order_id,
                                "sl_error": sl_error[:180],
                            },
                        )
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")
                    if closed:
                        _drop_pending_intent()
                    return "ENTRY_ABORTED_NO_HARD_SL"

                append_execution_event(
                    bot,
                    "ORDER_PROTECTION_ATTACHED",
                    {
                        "symbol": symbol,
                        "side": side,
                        "entry_client_order_id": entry_client_order_id,
                        "sl_client_order_id": sl_client_order_id,
                        "sl_exchange_order_id": sl_order.get("id"),
                        "sl_price": float(sl_val),
                    },
                )

                margin_used = float(final_usd) / max(float(current_leverage), 1)
                try:
                    send_telegram_msg(
                        f"\U0001f680 *\U0001f525 REAL TRADE ABIERTO*\n"
                        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                        f"\U0001f539 *{symbol}*\n"
                        f"\U0001f538 Lado: {side}\n"
                        f"\U0001f4b0 Precio: ${price}\n"
                        f"\U0001f4ca Notional: ${final_usd:.2f}\n"
                        f"\U0001f194 ID: {order.get('id', 'N/A')}\n"
                        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                        f"\U0001f4c8 *MERCADO*\n"
                        f"   RSI: {context.get('rsi', 0) if context else 0:.1f}\n"
                        f"   ADX: {context.get('adx', 0) if context else 0:.1f}\n"
                        f"   Tendencia: {context.get('trend', 'N/A') if context else 'N/A'}\n"
                        f"   SL: {sl_val:.4f} | TP: {tp_val:.4f}"
                    )
                    balance_lock = getattr(bot, "balance_lock", bot.lock)
                    with balance_lock:
                        bot.available_balance -= margin_used
                except Exception as entry_side_effect_err:
                    bot.log(f"⚠️ Entry side effect error (non-fatal): {entry_side_effect_err}")
            else:
                bot.log(f"❌ FALLO DE EJECUCIÓN: {symbol}")
                reject_reason = str(
                    getattr(bot.execution, "last_entry_reject_error", "") or "EXECUTION_FAILED"
                )[:220]
                append_execution_event(
                    bot,
                    "ENTRY_ORDER_REJECTED",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "reason": reject_reason,
                    },
                )
                _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")

                pending_state["status"] = TradeStatus.ENTRY_ACK_UNKNOWN.value
                pending_state["entry_reject_reason"] = reject_reason
                pending_state["intent_last_check_at_utc"] = utc_now_iso()
                pending_state["intent_check_attempts"] = (
                    int(pending_state.get("intent_check_attempts", 0) or 0) + 1
                )
                with bot.lock:
                    bot.active_trades[symbol] = pending_state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(symbol, pending_state)
                append_execution_event(
                    bot,
                    "ENTRY_ACK_UNKNOWN_PERSISTED",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "reason": reject_reason,
                    },
                )
                if not Config.PAPER_MODE:
                    bot.is_paused = True
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(symbol, pending_state)
                    return "ENTRY_ACK_UNKNOWN"
                _drop_pending_intent()
                return "EXECUTION_FAILED"
        elif not is_shadow and Config.PAPER_MODE:
            bot.log(f"\U0001f4dd PAPER TRADE (Simulado): {side} {symbol} (${final_usd:.2f})")
            append_execution_event(
                bot,
                "ORDER_FILLED",
                {
                    "symbol": symbol,
                    "side": side,
                    "is_shadow": False,
                    "simulated_real": True,
                    "entry_client_order_id": entry_client_order_id,
                    "filled_amount": float(amount),
                    "avg_fill_price": float(price),
                },
            )
            send_telegram_msg(
                f"\U0001f4dd *PAPER TRADE (SIMULACRO)*\n\U0001f539 {symbol}\n\U0001f538 Lado: {side}\n\U0001f4b0 Precio: {price}\n\U0001f4ca Notional: ${final_usd:.2f}\n⚠️ *AVISO:* Bot en modo PAPER."
            )
        else:
            bot.log(f"\U0001f47b SHADOW {side} {symbol} (${final_usd:.2f})")
            append_execution_event(
                bot,
                "ORDER_FILLED",
                {
                    "symbol": symbol,
                    "side": side,
                    "is_shadow": True,
                    "entry_client_order_id": entry_client_order_id,
                    "filled_amount": float(amount),
                    "avg_fill_price": float(price),
                },
            )
            send_telegram_msg(
                f"\U0001f47b *SHADOW TRADE ABIERTO*\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"\U0001f539 *{symbol}*\n"
                f"\U0001f538 Lado: {side}\n"
                f"\U0001f4b0 Precio: ${price}\n"
                f"\U0001f4ca Notional: ${final_usd:.2f}\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"   SL: {sl_val:.4f} | TP: {tp_val:.4f}"
            )

        _safe_update_signal_alert_status(bot, entry_client_order_id, "EXECUTED")
        bot.last_entry_open_ts = time.time()
        if is_shadow:
            bot.last_shadow_signal_ts = time.time()

        with bot.lock:
            if is_shadow and clean_snapshot:
                try:
                    bot.brain.save_trade_context_snapshot(
                        symbol=symbol,
                        side=side,
                        context_json=clean_snapshot,
                        entry_timestamp=utc_now_iso(),
                        is_shadow=True,
                    )
                except Exception as ctx_error:
                    bot.log(f"⚠️ Error guardando trade context snapshot: {ctx_error}")

            base_confidence = float((context or {}).get("prob_final", 75.0))
            adjusted_confidence = max(0.0, min(100.0, base_confidence + similarity_boost))

            trade_state = {
                "symbol": symbol,
                "side": side,
                "entry": float(avg_fill_price if not is_shadow else price),
                "pnl": 0.0,
                "amount": float(filled_amount if not is_shadow else amount),
                "requested_amount": float(requested_amount if not is_shadow else amount),
                "remaining_amount": float(remaining_amount if not is_shadow else 0.0),
                "notional_usd": float(final_usd),
                "size_usd": float(final_usd),
                "margin_used": float(margin_used),
                "margin_reserved": bool(simulated_margin_state.get("margin_reserved", False)),
                "margin_released": bool(simulated_margin_state.get("margin_released", False)),
                "sl": sl_val,
                "tp": tp_val,
                "trailing_active": False,
                "early_be_armed": False,
                "peak_pnl": 0.0,
                "open_time": utc_now_iso(),
                "is_shadow": is_shadow,
                "simulated_real": Config.PAPER_MODE and not is_shadow,
                "sector": "OTHE",
                "leverage": current_leverage,
                "market_snapshot": clean_snapshot,
                "entry_ob": ob_status,
                "entry_confidence": adjusted_confidence,
                "current_confidence": adjusted_confidence,
                "market_regime": entry_market_regime,
                "entry_shock_level": (context or {}).get("shock_level"),
                "entry_atr": (context or {}).get("atr", 0.0),
                "breakout_origin": bool((context or {}).get("breakout_ready", False)),
                "entry_client_order_id": entry_client_order_id,
                "sl_client_order_id": sl_client_order_id,
                "tp_client_order_id": tp_client_order_id,
                "entry_exchange_order_id": (order or {}).get("id") if not is_shadow else None,
                "sl_exchange_order_id": (sl_order or {}).get("id") if not is_shadow else None,
                "tp_exchange_order_id": None,
                "status": TradeStatus.PARTIAL_FILL_PENDING.value
                if (not is_shadow and remaining_amount > 0.0)
                else TradeStatus.OPEN.value,
                "partial_fill_pending": (not is_shadow and remaining_amount > 0.0),
                "partial_fill_started_at": utc_now_iso()
                if (not is_shadow and remaining_amount > 0.0)
                else None,
                "signal_ts": signal_ts,
                "similarity_boost": similarity_boost,
                "similarity_verdict": similarity_verdict,
            }

            if symbol not in bot.active_trades:
                bot.active_trades[symbol] = trade_state
                with bot.db_lock:
                    persisted = bot.brain.save_active_trade_state(symbol, trade_state)
                if not persisted:
                    _release_simulated_margin(bot, trade_state, 0.0)
                    bot.integrity_lock_active = True
                    bot.log(
                        f"\U0001f6d1 PERSISTENCE_GUARD {symbol}: orden aceptada pero estado activo no persistió. Integrity lock activado."
                    )
                    append_execution_event(
                        bot,
                        "ACTIVE_STATE_PERSIST_FAILED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "status": trade_state.get("status"),
                        },
                    )
                    send_telegram_msg(
                        f"\U0001f6a8 *PERSISTENCE GUARD*\n{symbol}: orden aceptada pero DB no persistió el estado activo. Integrity lock activado.",
                        Priority.CRITICAL,
                    )
                    return "PERSISTENCE_GUARD_ACTIVE"
                bot.log(
                    f"\U0001f4be CARTERA: {symbol} registrado ({'SHADOW' if is_shadow else 'REAL'})."
                )
                # --- INTEGRACIÓN KANBAN ---
                # Enviamos al background la creación de la tarjeta y guardado del item_id
                # Solo se crean tarjetas para trades REALES (no SHADOW)
                async_crear_tarjeta(
                    bot=bot,
                    symbol=symbol,
                    estrategia=trade_state.get("market_regime", "N/A"),
                    capital=float(final_usd),
                    is_shadow=is_shadow,
                )
            else:
                bot.active_trades[symbol].update(trade_state)

            cooldown_minutes = (
                Config.SHADOW_COOLDOWN_MINUTES if is_shadow else Config.TRADE_COOLDOWN_MINUTES
            )
            set_symbol_cooldown(bot, symbol, utc_now() + timedelta(minutes=cooldown_minutes))

            if not req_shadow and is_shadow:
                return f"OK_DEGRADED: {degradation_reason}"
            return "OK"
    except Exception as e:
        if simulated_margin_state is not None:
            _release_simulated_margin(bot, simulated_margin_state, 0.0)
        # CRITICAL: If we got to this point with a REAL order that may have filled,
        # we must HALT to prevent inconsistent state.
        if not is_shadow and order is not None:
            bot.is_paused = True
            bot.integrity_lock_active = True
            setattr(bot, "halt_system_active", True)
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "EXEC_EXCEPTION_POST_FILL_HALT",
                    {
                        "error": str(e)[:200],
                        "order": str(order)[:200],
                        "side": str(trade_state.get("side")),
                    },
                )
            send_telegram_msg(
                f"🛑 *EXEC_EXCEPTION_POST_FILL_HALT*\n{symbol} falló tras orden aceptada/consumo de margen. HALT activado.",
                Priority.CRITICAL,
            )
            append_execution_event(
                bot,
                "EXEC_EXCEPTION_POST_FILL_HALT",
                {"symbol": symbol, "error": str(e)[:200]},
            )
        bot.log(f"❌ RECHAZO {symbol}: {e}")
        if not is_shadow:
            send_telegram_msg(
                f"❌ *FALLO DE EJECUCIÓN (REAL)*\n{symbol} no pudo abrirse.\nError: {str(e)[:100]}"
            )
        _safe_update_signal_alert_status(bot, entry_client_order_id, "ERROR")
        with bot.db_lock:
            bot.brain.save_error_snapshot(
                symbol,
                "EXEC_EXCEPTION",
                bot.data_service.sanitize_context(context),
            )
        return f"ERROR: {str(e)[:20]}"

import logging
import threading
import time
from datetime import datetime

from config import Config
from core.cycle_context import CycleContext
from core.risk_engine import get_daily_pnl_pct
from core.risk_policy import activate_runtime_protection


def check_daily_drawdown_breaker(bot) -> bool:
    """Activa Circuit Breaker diario solo en REAL si supera pérdida UTC."""
    if bool(getattr(Config, "PAPER_MODE", True)):
        return False

    db_path = getattr(getattr(bot, "brain", None), "db_name", "sniper_brain.db")
    try:
        wallet_balance = float(bot.get_current_balance() or 0.0)
    except Exception as error:
        bot.log(f"⚠️ DAILY_DRAWDOWN_BALANCE_UNAVAILABLE: {error}")
        wallet_balance = float(getattr(bot, "balance", 0.0) or 0.0)

    daily_pnl_pct, daily_pnl_usd = get_daily_pnl_pct(db_path, wallet_balance)
    if daily_pnl_pct is None or daily_pnl_usd is None:
        msg = "CRITICAL: Daily drawdown unverifiable; REAL entries blocked pending manual intervention"
        logging.getLogger("SniperAI").critical(msg)
        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            pause=True,
            log_message=f"🚨 {msg}",
            telegram_message=(
                "🚨 *PÁNICO: DRAWDOWN NO VERIFICABLE*\n"
                "No se pudo verificar la pérdida diaria REAL UTC.\n"
                "Nuevas entradas REAL bloqueadas hasta intervención manual."
            ),
            alert_once_attr="daily_drawdown_alert_sent",
            reason="DAILY_DRAWDOWN_UNVERIFIED",
            source="daily_drawdown_breaker",
            extra={"wallet_balance": float(wallet_balance)},
        )
        return True
    max_drawdown = float(getattr(Config, "MAX_DAILY_DRAWDOWN_PCT", 0.03) or 0.03)
    if daily_pnl_pct > -max_drawdown:
        return False

    msg = (
        "CRITICAL: Circuit Breaker Active "
        f"daily_pnl={daily_pnl_pct * 100:.2f}% "
        f"usd=${daily_pnl_usd:.2f} limit=-{max_drawdown * 100:.2f}%"
    )
    logging.getLogger("SniperAI").critical(msg)
    activate_runtime_protection(
        bot,
        circuit_breaker=True,
        pause=True,
        log_message=f"🚨 {msg}",
        telegram_message=(
            "🚨 *PÁNICO: CIRCUIT BREAKER ACTIVO*\n"
            f"Pérdida diaria REAL UTC: {daily_pnl_pct * 100:.2f}% "
            f"(${daily_pnl_usd:.2f})\n"
            f"Límite: -{max_drawdown * 100:.2f}%\n"
            "Nuevas entradas REAL bloqueadas hasta intervención manual."
        ),
        alert_once_attr="daily_drawdown_alert_sent",
        reason="DAILY_DRAWDOWN_LIMIT_REACHED",
        source="daily_drawdown_breaker",
        extra={
            "daily_pnl_pct": float(daily_pnl_pct),
            "daily_pnl_usd": float(daily_pnl_usd),
            "max_drawdown": float(max_drawdown),
        },
    )
    return True


def run_main_logic(bot):
    last_report_time = time.time()
    last_coach_time = time.time()
    last_log_check = time.time()

    bot.init_complete.wait()

    ws_manager = getattr(bot, "ws_manager", None)
    if ws_manager and getattr(ws_manager, "is_running", False) is False:
        ws_manager.start_background()

    threading.Thread(target=bot._guardian_loop, daemon=True).start()

    while bot.is_running:
        try:
            bot._refresh_symbol_controls_if_due()
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)):
                cleaned = bot.breakout_agent.clean_stale_watchlist()
                if cleaned > 0:
                    bot.log(
                        f"🧹 BREAKOUT_WATCH cleaned={cleaned} remaining={bot.breakout_agent.size()}"
                    )

            if bot._run_crash_predictor_cycle():
                continue

            now = datetime.now()
            bot.check_weekly_schedule()
            bot.check_weekly_maintenance_utc()
            try:
                base_bal_safe = (
                    bot.daily_initial_balance if bot.daily_initial_balance > 0 else bot.balance
                )
                pnl_real_safe, _ = bot.brain.get_daily_real_pnl(base_bal_safe)
                bot.check_safety_and_goals(current_pnl=pnl_real_safe)
            except Exception as e_safety:
                bot.log(f"⚠️ check_safety_and_goals error (non-fatal): {e_safety}")

            bot.last_radar_update = time.time()

            bot._run_market_refresh_cycle()
            triage_snapshot, tickers = bot._run_triage_cycle()

            if time.time() - getattr(bot, "last_pm_check", 0) > 300:
                bot._perform_post_mortem()
                bot.last_pm_check = time.time()

            last_report_time, last_coach_time, last_log_check = bot._run_periodic_housekeeping(
                now,
                last_report_time,
                last_coach_time,
                last_log_check,
            )

            bot._run_btc_panic_cycle()

            if not tickers:
                bot.log("⚠️ No se pudieron obtener precios. Reintentando en 10s...")
                time.sleep(10)
                continue

            if not bot.ml_healthy and Config.ML_HEALTH_VETO_ENABLED:
                bot.log("🛑 VETO ML ACTIVO: Saltando escaneo de señales...")
                time.sleep(60)
                continue

            if not bot.pairs_to_scan:
                bot.log("⚠️ Lista de objetivos vacía tras triaje. Esperando siguiente ciclo...")
                bot._run_cycle_wait_and_api_log()
                continue

            signal_stats = {
                "BUY": 0,
                "SELL": 0,
                "NEUTRAL": 0,
                "VETO": 0,
                "SHADOW": 0,
                "REAL": 0,
            }

            bot.log(f"📡 Radar: Escaneando {len(bot.pairs_to_scan)} pares...")

            pnl_real_hoy = bot._run_market_context_cycle(tickers)

            top_triage = bot._prepare_top_triage(triage_snapshot)
            if not top_triage:
                bot.log("⚠️ Sin candidatos válidos tras cuarentena. Esperando siguiente ciclo...")
                bot._run_cycle_wait_and_api_log()
                continue

            results = bot._fetch_triage_data_parallel(top_triage)
            if check_daily_drawdown_breaker(bot):
                bot._finalize_scan_cycle(signal_stats)
                bot._run_cycle_wait_and_api_log()
                continue

            bot.cycle_context = CycleContext.capture(
                bot, tickers=tickers, pnl_real_hoy=pnl_real_hoy
            )

            bot._run_signal_scan_cycle(top_triage, results, signal_stats, pnl_real_hoy)

            bot._finalize_scan_cycle(signal_stats)
            bot._run_cycle_wait_and_api_log()

        except Exception as error:
            bot.log(f"🚨 Error recuperado: {str(error)}. El escaneo continúa...")
            time.sleep(10)

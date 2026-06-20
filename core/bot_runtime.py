import sys
import threading
import time

import pandas as pd

from config import Config
from core.cmd_consumer import consume_command_file
from core.real_auth_health import maybe_check_real_auth
from core.reconciliation import reconcile_bootstrap_state
from core.watchdog import write_watchdog_heartbeat


def run_initial_load(bot, dashboard_module):
    try:
        bot.connect()
        bot.acquire_targets()
        bot._load_ai_restrictions()
        reconcile_bootstrap_state(bot)

        bot.log("🔍 Ejecutando auto-blacklist de poor performers...")
        bot.brain.auto_blacklist_poor_performers(min_trades=5, max_loss_pct=-5.0, max_wr=40.0)

        bot.check_for_evolution()

        if dashboard_module:
            try:
                threading.Thread(
                    target=dashboard_module.start_dashboard,
                    args=(bot,),
                    daemon=True,
                ).start()
                bot.log("🖥️ Dashboard iniciado en segundo plano.")
            except Exception as error:
                bot.log(f"⚠️ Error Dashboard: {error}")

        if Config.MAX_SHADOW_TRADES <= 5 and not Config.PAPER_MODE:
            bot.log(
                f"⚠️ ADVERTENCIA DE CONFIGURACIÓN: MAX_SHADOW_TRADES está en {Config.MAX_SHADOW_TRADES}. "
                "Esto limita severamente la capacidad de exploración. Considere un valor >= 20."
            )

        if not isinstance(bot.balance, (int, float)) or pd.isna(bot.balance):
            blk = getattr(bot, "balance_lock", bot.lock)
            with blk:
                bot.balance = 0.0

        bot.init_complete.set()
        bot.log("🚀 Sistema inicializado. Iniciando bucles de trabajo...")

        threading.Thread(target=bot._main_logic, daemon=True).start()
        threading.Thread(target=bot._telegram_listener, daemon=True).start()
        threading.Thread(target=bot._terminal_command_listener, daemon=True).start()
        threading.Thread(target=bot.start_silent_sync, daemon=True).start()
        threading.Thread(target=bot._runtime_monitor_loop, daemon=True).start()
        threading.Thread(target=bot._start_state_snapshot_loop, daemon=True).start()

    except Exception as error:
        bot.startup_error = error
        bot.log(f"❌ FALLO CRÍTICO EN CARGA: {error}")
        bot.is_running = False
        shutdown_event = getattr(bot, "_shutdown_event", None)
        if shutdown_event is not None:
            shutdown_event.set()
        bot.init_complete.set()


def run_bot_runtime_loop(bot, dashboard_module, logger, shadow_logger):
    if not sys.stdout.isatty() and not getattr(Config, "FORCE_UI", False):
        print(
            """
╔═══════════════════════════════════════════════════════════════════════════╗
║             🏆 SNIPER AI v118 - MODO TRINITY 🏆                      ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  🤖 Trinity: [MT][SR][G] + RAG + SHOCK                                  ║
║  🧠 Consensus NN 1H + Ghost Ensemble + Risk Engine                       ║
║  📊 Filtros: Liquidez | Spread | SHOCK | Latencia                        ║
║  🛡️ Protecciones: TP/SL dinámico | Daily guard | Cooldowns               ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  💰 BALANCE: $20.00 | PnL Hoy: +0.00% | Target: 5%                    ║
║  📈 REAL: 0 | SHADOW: 0 | WR: 0% | SCAN: 0 pares                     ║
║  ⚡ ACTIVO: Escaneando mercados...                                    ║
╚═══════════════════════════════════════════════════════════════════════════╝
            """
        )

    bot.ui.start()

    threading.Thread(target=bot._initial_load, args=(dashboard_module,), daemon=True).start()

    try:
        while bot.is_running:
            try:
                consume_command_file(bot)
                maybe_check_real_auth(bot)
                telemetry = bot._collect_telemetry()

                ml_metrics = {}
                if bot.ml_monitor:
                    ml_metrics = bot.ml_monitor.get_all_metrics()

                if hasattr(bot, "ml_performance") and bot.ml_performance:
                    try:
                        ml_metrics["performance"] = bot.ml_performance.calculate_metrics()
                        ml_metrics["top_symbols"] = bot.ml_performance.get_top_symbols(
                            min_predictions=3
                        )
                    except Exception as error_ml:
                        logger.warning(f"⚠️ Error en métricas ML: {error_ml}")

                with bot.lock:
                    trades_snapshot = list(bot.active_trades.values()) if hasattr(bot, "active_trades") else []
                closed_snapshot = list(bot.recent_closed_trades) if hasattr(bot, "recent_closed_trades") else []
                slock = getattr(bot, "scanner_lock", None)
                if slock:
                    with slock:
                        scanner_snapshot = bot.scanner_history[:50] if hasattr(bot, "scanner_history") else []
                else:
                    scanner_snapshot = bot.scanner_history[:50] if hasattr(bot, "scanner_history") else []
                bot.ui.update(
                    balance=bot.balance,
                    trades=trades_snapshot,
                    recent_closed_trades=closed_snapshot,
                    scanner=scanner_snapshot,
                    db_stats=telemetry,
                    sentiment=getattr(bot, "current_sentiment", "NEUTRAL"),
                    ml_metrics=ml_metrics,
                )
                if Config.ENABLE_UI:
                    bot.ui.render()
            except Exception as error_ui:
                logger.error(f"❌ UI ERROR: {error_ui}")
                if bot.is_running:
                    time.sleep(5)
            try:
                write_watchdog_heartbeat(bot)
            except Exception as hb_error:
                logger.warning(f"⚠️ Heartbeat watchdog falló: {hb_error}")
            time.sleep(1)
    except KeyboardInterrupt:
        bot.is_running = False
        shutdown_event = getattr(bot, "_shutdown_event", None)
        if shutdown_event is not None:
            shutdown_event.set()
        bot.ui.stop()
        bot.log("🛑 Guardando caché y forzando flasheo de Shadow Logs...")
        bot.save_cache(blocking=True)
        shadow_logger.stop()
        bot.log("✅ Caché y Logs guardados.")

    startup_error = getattr(bot, "startup_error", None)
    if startup_error is not None:
        print(f"\n❌ FALLO CRÍTICO EN CARGA: {startup_error}", flush=True)
        raise RuntimeError(f"BOOTSTRAP_FAILED: {startup_error}") from startup_error

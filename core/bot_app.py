"""
SNIPER AI v118 - Aplicacion principal del bot.
"""

import asyncio
import importlib.util
import logging
import signal
import sys
import threading
import time
import traceback
import warnings
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from typing import Any

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

try:
    from core.api_weight_tracker import BinanceWeightTracker

    HAS_WEIGHT_TRACKER = True
except ImportError:
    BinanceWeightTracker = None  # type: ignore[assignment, misc]
    HAS_WEIGHT_TRACKER = False
    logging.getLogger("SniperAI").warning(
        "BinanceWeightTracker no disponible — límites de API weight no se aplicarán."
    )

try:
    import tensorflow as tf
except ImportError:
    tf = None

from config import Config
from core.analytics.fvg_tracker import run_fvg_tracker_loop
from core.bot_audit_verdict import get_audit_verdict as resolve_audit_verdict
from core.bot_balance_ops import (
    get_current_balance as fetch_current_balance,
)
from core.bot_balance_ops import (
    handle_reset_pnl as run_handle_reset_pnl,
)
from core.bot_balance_ops import (
    start_silent_sync as run_start_silent_sync,
)
from core.bot_cli_ops import prioritize_targets, terminal_command_listener
from core.bot_connection import connect_to_binance
from core.bot_consensus_display import (
    render_consensus_telemetry as show_consensus_telemetry,
)
from core.bot_core_setup import init_core_services_and_engines
from core.bot_cycles import (
    fetch_triage_data_parallel,
    finalize_scan_cycle,
    prepare_top_triage,
    run_cycle_wait_and_api_log,
    run_market_context_cycle,
    run_market_refresh_cycle,
    run_triage_cycle,
)
from core.bot_guardian import run_guardian_loop
from core.bot_housekeeping import run_periodic_housekeeping
from core.bot_initialization import (
    init_realtime_and_monitoring,
    init_runtime_state,
)
from core.bot_io_loops import (
    perform_post_mortem,
    telegram_listener,
    websocket_monitor,
)
from core.bot_main_loop import run_main_logic
from core.bot_maintenance import backup_database_placeholder, check_for_evolution
from core.bot_market_state import detect_market_regime, warmup_hmm_regime
from core.bot_misc_ops import (
    get_vol_24h as resolve_vol_24h,
)
from core.bot_misc_ops import (
    handle_command as dispatch_command,
)
from core.bot_misc_ops import (
    load_ai_restrictions,
)
from core.bot_misc_ops import (
    self_adjust_exigency as adjust_exigency,
)
from core.bot_ml_health import check_ml_models_health
from core.bot_ml_runtime import check_recent_mfe_health, init_ml_monitoring
from core.bot_models_startup import init_models_and_startup_tasks
from core.bot_pair_fetch import fetch_pair_data as run_fetch_pair_data
from core.bot_performance_ops import (
    get_ob_efficiency_report as build_ob_efficiency_report,
)
from core.bot_performance_ops import (
    perform_healthcheck as run_healthcheck,
)
from core.bot_performance_ops import (
    update_dynamic_risk as run_update_dynamic_risk,
)
from core.bot_post_exit_analysis import calc_post_exit_drift, load_local_candles
from core.bot_radar import update_radar as run_update_radar
from core.bot_risk_cycles import run_btc_panic_cycle, run_crash_predictor_cycle
from core.bot_runtime import run_bot_runtime_loop, run_initial_load
from core.bot_runtime_monitor import (
    append_runtime_metric,
    get_rss_mb,
    run_runtime_monitor_loop,
)
from core.bot_runtime_ops import (
    check_instinctive_safety as run_check_instinctive_safety,
)
from core.bot_runtime_ops import (
    close_all_positions_emergency,
    heartbeat_loop,
)
from core.bot_runtime_safety import check_safety_and_goals as evaluate_safety_and_goals
from core.bot_scorecard import (
    maybe_send_daily_exit_scorecard,
    send_daily_exit_scorecard,
)
from core.bot_shutdown import request_graceful_shutdown
from core.bot_signals import run_signal_scan_cycle
from core.bot_symbol_controls import (
    get_cached_btc_data,
    get_cached_funding_rate,
    load_runtime_symbol_controls,
    refresh_symbol_controls_if_due,
)
from core.bot_telemetry import collect_telemetry
from core.bot_trade_entry import execute_order as run_execute_order
from core.bot_trade_monitor import monitor_open_trades as run_monitor_open_trades
from core.bot_wallet_sync import sync_wallet as run_wallet_sync
from core.bot_weekly_ops import check_weekly_maintenance_utc, check_weekly_schedule
from core.command_router import handle_basic_command
from core.execution_runtime_state import persist_execution_runtime_state
from core.market_intelligence import acquire_targets, get_active_market_snapshot
from core.process_lock import acquire_single_instance_lock
from core.signals.analyze import _analyze_symbol_candidate
from core.signals.context import _build_symbol_context, _update_signal_diagnostics
from core.signals.execution import _execute_and_update_symbol
from core.signals.filters import (
    _apply_entry_filters_and_adjust_prob,
    _plan_execution_mode,
    _resolve_audit_verdict_and_stats,
)
from core.state_snapshot import start_state_snapshot_loop as run_start_state_snapshot_loop
from core.strategy.shocks import next_shock_distance_pct
from core.trade_manager import abort_partial_trade as tm_abort_partial_trade
from core.trade_manager import close_trade as tm_close_trade
from tools.learning import Brain, shadow_logger
from tools.notifier import send_telegram_msg
from tools.ui import UI
from tools.ws_manager import BinanceWebSocket

try:
    from tools.export_master_dataset import export_dataset
except ImportError:
    export_dataset = None  # type: ignore[assignment]


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


logger = logging.getLogger("SniperAI")
logger.setLevel(logging.INFO)
if not logger.handlers:
    log_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    log_formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log_handler.setFormatter(log_formatter)
    logger.addHandler(log_handler)


def _backup_database_placeholder():
    return backup_database_placeholder()


backup_database = _backup_database_placeholder

try:
    import tools.dashboard as dashboard
except (ImportError, ModuleNotFoundError) as error:
    print(f"⚠️ Dashboard no disponible: {error}")
    dashboard = None  # type: ignore[assignment]

try:
    from tools.ml_monitor import MLMonitor

    ML_MONITOR_AVAILABLE = True
except ImportError:
    ML_MONITOR_AVAILABLE = False
    MLMonitor = None  # type: ignore[assignment, misc]
    print("⚠️ ML Monitor no disponible")


class Bot:
    def __init__(self):
        self.is_running = True
        self.ui = UI()
        self.brain = Brain()
        self.main_loop = None  # [SRE] Referencia al Global Event Loop
        self._backup_database_fn = backup_database
        self._ml_monitor_available = ML_MONITOR_AVAILABLE
        self._dashboard_module = dashboard
        self._logger = logger
        self._shadow_logger = shadow_logger
        self._main_loop_thread = None
        self._main_loop_ready = threading.Event()

        self._bind_main_loop_or_abort()

        self._init_core_services_and_engines()
        self._init_runtime_state()
        self._warmup_hmm_regime()
        self._init_realtime_and_monitoring()
        self._init_models_and_startup_tasks()

    def _delegate(self, fn, /, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def _bind_main_loop_or_abort(self):
        if (
            getattr(self, "main_loop", None) is not None
            and not self.main_loop.is_closed()
            and self.main_loop.is_running()
        ):
            return

        loop = asyncio.new_event_loop()
        self.main_loop = loop

        def _run_loop_forever():
            try:
                asyncio.set_event_loop(loop)
                self._main_loop_ready.set()
                loop.run_forever()
            except Exception as error:
                logger.critical(f"🚨 FATAL BOOT ERROR: Event Loop thread falló: {error}")
            finally:
                try:
                    loop.close()
                except Exception as error:
                    logger.warning(f"⚠️ No se pudo cerrar event loop principal: {error}")

        self._main_loop_thread = threading.Thread(
            target=_run_loop_forever,
            daemon=True,
            name="sniper-main-loop",
        )
        self._main_loop_thread.start()

        if not self._main_loop_ready.wait(timeout=2.0):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no pudo inicializarse en tiempo. Abortando arranque."
            )
            raise SystemExit(1)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not loop.is_running():
            time.sleep(0.02)

        if (
            not getattr(self, "main_loop", None)
            or self.main_loop.is_closed()
            or not self.main_loop.is_running()
        ):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no está enlazado a la instancia del Bot. Abortando arranque."
            )
            raise SystemExit(1)

    def _init_core_services_and_engines(self):
        return self._delegate(init_core_services_and_engines)

    def _init_runtime_state(self):
        return init_runtime_state(
            self,
            has_weight_tracker=HAS_WEIGHT_TRACKER,
            weight_tracker_cls=BinanceWeightTracker,
        )

    def _init_realtime_and_monitoring(self):
        return init_realtime_and_monitoring(
            self,
            websocket_cls=BinanceWebSocket,
            ml_monitor_available=ML_MONITOR_AVAILABLE,
            ml_monitor_cls=MLMonitor,
        )

    def _init_models_and_startup_tasks(self):
        return init_models_and_startup_tasks(
            self,
            export_dataset_fn=export_dataset,
            backup_database_fn=backup_database,
            tf_module=tf,
        )

    def render_consensus_telemetry(self, symbol, p_final, modo, votos, regime=None):
        return self._delegate(show_consensus_telemetry, symbol, p_final, modo, votos, regime)

    def _load_ai_restrictions(self):
        return self._delegate(load_ai_restrictions)

    def self_adjust_exigency(self):
        return self._delegate(adjust_exigency)

    @staticmethod
    @lru_cache(maxsize=512)
    def _get_base_coin(symbol):
        clean_symbol = symbol.split(":")[0]
        base = clean_symbol.split("/")[0]
        return base

    def _get_vol_24h(self, symbol, tickers):
        return resolve_vol_24h(symbol, tickers)

    def _init_ml_monitoring(self):
        return self._delegate(init_ml_monitoring, ML_MONITOR_AVAILABLE)

    def _check_ml_models_health(self):
        return self._delegate(check_ml_models_health, ML_MONITOR_AVAILABLE)

    def _heartbeat_loop(self):
        return self._delegate(heartbeat_loop)

    def _websocket_monitor(self):
        return self._delegate(websocket_monitor)

    def check_for_evolution(self):
        return self._delegate(check_for_evolution)

    def log(self, msg):
        self.logs.append(msg)
        logger.info(msg)

    def _get_rss_mb(self) -> float:
        return self._delegate(get_rss_mb)

    def _append_runtime_metric(self, payload: dict[str, Any]) -> None:
        return self._delegate(append_runtime_metric, payload)

    def _runtime_monitor_loop(self):
        return self._delegate(run_runtime_monitor_loop)

    def _collect_telemetry(self) -> dict:
        return self._delegate(collect_telemetry, logger)

    def _get_market_regime(self) -> str:
        return self._delegate(detect_market_regime)

    def _warmup_hmm_regime(self) -> bool:
        return self._delegate(warmup_hmm_regime)

    def connect(self):
        return self._delegate(connect_to_binance)

    def sync_wallet(self):
        return self._delegate(run_wallet_sync)

    def check_instinctive_safety(self, symbol, context):
        return self._delegate(run_check_instinctive_safety, symbol, context)

    def _close_all_positions_emergency(self):
        return self._delegate(close_all_positions_emergency)

    def _update_dynamic_risk(self):
        return self._delegate(run_update_dynamic_risk)

    def monitor_open_trades(self):
        return self._delegate(run_monitor_open_trades)

    def _guardian_loop(self):
        return self._delegate(run_guardian_loop)

    def ai_coach_allows_escalation(self):
        if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
            return False
        return True

    def check_safety_and_goals(self, current_pnl=None):
        return self._delegate(evaluate_safety_and_goals, current_pnl=current_pnl)

    def start_silent_sync(self):
        return self._delegate(run_start_silent_sync)

    def _start_state_snapshot_loop(self):
        return self._delegate(run_start_state_snapshot_loop)

    def _fvg_tracker_loop(self):
        if hasattr(self, "fvg_tracker") and self.fvg_tracker.enabled:
            return self._delegate(run_fvg_tracker_loop)

    def get_current_balance(self):
        return self._delegate(fetch_current_balance)

    def handle_reset_pnl(self):
        return self._delegate(run_handle_reset_pnl)

    def perform_healthcheck(self):
        return self._delegate(run_healthcheck)

    def get_ob_efficiency_report(self):
        return self._delegate(build_ob_efficiency_report)

    def check_weekly_schedule(self):
        return check_weekly_schedule(self, _module_available)

    def check_weekly_maintenance_utc(self):
        return self._delegate(check_weekly_maintenance_utc)

    def handle_command(self, text: str):
        return dispatch_command(
            self,
            text=text,
            handle_basic_command_fn=handle_basic_command,
            export_dataset_fn=export_dataset,
            notify_fn=send_telegram_msg,
        )

    def _telegram_listener(self):
        return self._delegate(telegram_listener)

    def _terminal_command_listener(self):
        return self._delegate(terminal_command_listener)

    def _perform_post_mortem(self):
        return self._delegate(perform_post_mortem)

    def _prioritize_targets(self):
        return self._delegate(prioritize_targets)

    def _load_runtime_symbol_controls(self):
        return self._delegate(load_runtime_symbol_controls)

    def _refresh_symbol_controls_if_due(self):
        return self._delegate(refresh_symbol_controls_if_due)

    def _get_cached_funding_rate(self, symbol):
        return self._delegate(get_cached_funding_rate, symbol)

    def _get_cached_btc_data(self):
        return self._delegate(get_cached_btc_data)

    def _load_local_candles(self, symbol, timeframe="1h"):
        return load_local_candles(symbol, timeframe)

    def _calc_post_exit_drift(self, symbol, side, exit_ts_iso, exit_price, lookahead_bars=4):
        return calc_post_exit_drift(
            symbol=symbol,
            side=side,
            exit_ts_iso=exit_ts_iso,
            exit_price=exit_price,
            lookahead_bars=lookahead_bars,
        )

    def _check_recent_mfe_health(self):
        return self._delegate(check_recent_mfe_health)

    def _send_daily_exit_scorecard(self):
        return self._delegate(send_daily_exit_scorecard)

    def _maybe_send_daily_exit_scorecard(self):
        return self._delegate(maybe_send_daily_exit_scorecard)

    def _fetch_pair_data(self, symbol):
        return self._delegate(run_fetch_pair_data, symbol)

    def _initial_load(self, dashboard_module=None):
        if dashboard_module is None:
            dashboard_module = getattr(self, "_dashboard_module", None)
        return run_initial_load(self, dashboard_module)

    def run(self):
        return run_bot_runtime_loop(
            self,
            getattr(self, "_dashboard_module", None),
            getattr(self, "_logger", None),
            getattr(self, "_shadow_logger", None),
        )

    def _run_periodic_housekeeping(
        self,
        now,
        last_report_time,
        last_coach_time,
        last_log_check,
    ):
        return run_periodic_housekeeping(
            self,
            now,
            last_report_time,
            last_coach_time,
            last_log_check,
        )

    def acquire_targets(self):
        return acquire_targets(self)

    def _get_active_market_snapshot(self, pool_limit=None):
        return get_active_market_snapshot(self, pool_limit=pool_limit)

    def _run_market_refresh_cycle(self):
        return run_market_refresh_cycle(self)

    def _run_triage_cycle(self):
        return run_triage_cycle(self)

    def _run_market_context_cycle(self, tickers):
        return run_market_context_cycle(self, tickers)

    def _run_crash_predictor_cycle(self) -> bool:
        return run_crash_predictor_cycle(self)

    def _run_btc_panic_cycle(self):
        return run_btc_panic_cycle(self)

    def _prepare_top_triage(self, triage_snapshot):
        return prepare_top_triage(self, triage_snapshot)

    def _fetch_triage_data_parallel(self, top_triage):
        return fetch_triage_data_parallel(self, top_triage)

    def _analyze_symbol_candidate(self, symbol_raw, symbol, df_main, df_4h, elapsed):
        return _analyze_symbol_candidate(self, symbol_raw, symbol, df_main, df_4h, elapsed)

    def _build_symbol_context(self, symbol_raw, symbol, df_main, price, ind, audit_signal):
        return _build_symbol_context(self, symbol_raw, symbol, df_main, price, ind, audit_signal)

    def _execute_and_update_symbol(
        self,
        symbol_raw,
        symbol,
        audit_signal,
        prob_final,
        audit_verdict,
        should_execute,
        is_shadow_exec,
        df_main,
        ctx,
        ob_status,
        votos,
        decision,
        elapsed,
    ):
        return _execute_and_update_symbol(
            self,
            symbol_raw,
            symbol,
            audit_signal,
            prob_final,
            audit_verdict,
            should_execute,
            is_shadow_exec,
            df_main,
            ctx,
            ob_status,
            votos,
            decision,
            elapsed,
        )

    def _update_signal_diagnostics(
        self, symbol, audit_signal, prob_final, mode, votos, ind, signal_stats
    ):
        return _update_signal_diagnostics(
            self, symbol, audit_signal, prob_final, mode, votos, ind, signal_stats
        )

    def _apply_entry_filters_and_adjust_prob(
        self, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel, votos=None
    ):
        return _apply_entry_filters_and_adjust_prob(
            self, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel, votos=votos
        )

    def _plan_execution_mode(
        self,
        symbol,
        audit_signal,
        prob_final,
        audit_verdict,
        filter_passed,
        filter_reason,
        ctx,
    ):
        return _plan_execution_mode(
            self,
            symbol,
            audit_signal,
            prob_final,
            audit_verdict,
            filter_passed,
            filter_reason,
            ctx,
        )

    def _resolve_audit_verdict_and_stats(
        self,
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
        return _resolve_audit_verdict_and_stats(
            self,
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
        )

    def _run_signal_scan_cycle(self, top_triage, results, signal_stats, pnl_real_hoy):
        return run_signal_scan_cycle(self, top_triage, results, signal_stats, pnl_real_hoy)

    def _finalize_scan_cycle(self, signal_stats):
        return finalize_scan_cycle(self, signal_stats)

    def _run_cycle_wait_and_api_log(self):
        return run_cycle_wait_and_api_log(self)

    def _main_logic(self):
        return run_main_logic(self)

    def _perform_triage(self):
        return self._get_active_market_snapshot()

    def save_cache(self, blocking=False):
        try:
            if hasattr(self, "data_service") and self.data_service:
                if blocking or not hasattr(self.data_service, "save_cache_async"):
                    self.data_service.save_cache()
                else:
                    self.data_service.save_cache_async()
            persist_execution_runtime_state(self)
        except Exception as error:
            self.log(f"⚠️ Error al guardar caché: {error}")

    def get_audit_verdict(
        self,
        symbol,
        prob_ia,
        signal,
        ob_status,
        pnl_hoy,
        meta_actual,
        mode="NONE",
        ctx=None,
    ):
        return resolve_audit_verdict(
            self,
            symbol=symbol,
            prob_ia=prob_ia,
            signal=signal,
            ob_status=ob_status,
            pnl_hoy=pnl_hoy,
            meta_actual=meta_actual,
            mode=mode,
            ctx=ctx,
        )

    def update_radar(
        self,
        symbol,
        decision,
        prob_ia,
        ob_status,
        audit_verdict,
        ctx,
        votos=None,
        response_ms=-1,
    ):
        return run_update_radar(
            self,
            symbol,
            decision,
            prob_ia,
            ob_status,
            audit_verdict,
            ctx,
            votos=votos,
            response_ms=response_ms,
        )

    def execute_order(
        self,
        symbol,
        side,
        price,
        atr,
        is_shadow=False,
        vol=0.0,
        context=None,
        ob_status="⚪",
        override_usd_size=0.0,
    ):
        return run_execute_order(
            self,
            symbol=symbol,
            side=side,
            price=price,
            atr=atr,
            is_shadow=is_shadow,
            vol=vol,
            context=context,
            ob_status=ob_status,
            override_usd_size=override_usd_size,
        )

    def close_trade(
        self,
        symbol,
        reason,
        exit_price,
        exit_confidence=0.0,
        latency_context=None,
    ):
        tm_close_trade(
            self,
            symbol=symbol,
            reason=reason,
            exit_price=exit_price,
            exit_confidence=exit_confidence,
            latency_context=latency_context,
        )

    def abort_partial_trade(self, symbol, reason, exit_price):
        tm_abort_partial_trade(
            self,
            symbol=symbol,
            reason=reason,
            exit_price=exit_price,
        )

    def _safe_div(self, a, b):
        try:
            return float(a) / float(b) if float(b) != 0 else 0.0
        except Exception:
            return 0.0

    def _get_shock_distance_pct(self, df, side):
        try:
            return next_shock_distance_pct(
                df=df,
                side=side,
                pivot_window=int(getattr(Config, "SHOCK_PIVOT_WINDOW", 3)),
                lookback_bars=int(getattr(Config, "SHOCK_LOOKBACK_BARS", 240)),
            )
        except Exception:
            return None, None

    def _update_scanner_status(self, symbol, status, qoe="--"):
        self.update_radar(
            symbol,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            status,
            {"tier": "IRON"},
            response_ms=-1,
        )


def _check_real_mode_guardrails():
    errors = []
    if not Config.PAPER_MODE:
        if not Config.ALLOW_REAL_TRADING:
            errors.append("ALLOW_REAL_TRADING=false: modo REAL no permitido.")
        if Config.USE_TESTNET:
            errors.append("USE_TESTNET=true con PAPER_MODE=false es incoherente.")
        if Config.MAX_OPEN_TRADES > 3:
            errors.append("MAX_OPEN_TRADES debe ser <= 3 en modo REAL.")
        if Config.MAX_RISK_USD > 50:
            errors.append("MAX_RISK_USD debe ser <= 50 en modo REAL.")
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM no configurado: obligatorio en modo REAL.")
        logger.warning(
            "🔥 MODO REAL ACTIVADO - El bot operará con capital real. "
            "Verifique que las configuraciones de riesgo sean apropiadas."
        )
    else:
        mode_detail = "TESTNET" if Config.USE_TESTNET else "PAPER (simulado)"
        logger.info(f"📝 MODO {mode_detail} - Sin riesgo de capital real.")
    if errors:
        raise RuntimeError("REAL_MODE_GUARDRAILS: " + "; ".join(errors))


def run_entrypoint():
    try:
        if not acquire_single_instance_lock(logger):
            raise SystemExit(1)

        for warning in Config.env_warnings():
            logger.warning(f"⚠️ CONFIG_ENV_FALLBACK: {warning}")
        config_errors = Config.validate()
        if config_errors:
            raise RuntimeError("CONFIG_VALIDATION_FAILED: " + "; ".join(config_errors))

        mode_str = "REAL" if not Config.PAPER_MODE else "PAPER"
        logger.info(f"📋 CONFIG LOADED: mode={mode_str}")
        logger.info(
            f"   RISK_PER_TRADE: {Config.RISK_PER_TRADE_PCT * 100:.2f}% | MAX_OPEN_TRADES: {Config.MAX_OPEN_TRADES}"
        )
        logger.info(
            f"   MAX_RISK_USD: ${Config.MAX_RISK_USD:.2f} | DAILY_LOSS_LIMIT: {Config.DAILY_LOSS_LIMIT:.2f}%"
        )
        logger.info(f"   SHOCK_MIN_DIST: {Config.SHOCK_MIN_DIST_PCT:.2f}%")

        _check_real_mode_guardrails()

        bot = Bot()
        if (
            not getattr(bot, "main_loop", None)
            or bot.main_loop.is_closed()
            or not bot.main_loop.is_running()
        ):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no está enlazado a la instancia del Bot. Abortando arranque."
            )
            sys.exit(1)

        def _graceful_shutdown(signum, _frame):
            signal_name = "SIGINT" if signum == getattr(signal, "SIGINT", -1) else "SIGTERM"
            logger.warning(f"⚠️ Señal {signal_name} recibida. Iniciando apagado ordenado...")
            request_graceful_shutdown(bot, reason=signal_name, logger=logger)

        signal.signal(signal.SIGINT, _graceful_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _graceful_shutdown)

        bot.run()

        if getattr(bot, "shutdown_in_progress", False):
            shutdown_done = bool(
                getattr(bot, "shutdown_complete", None) and bot.shutdown_complete.wait(timeout=85)
            )
            if not shutdown_done:
                logger.warning(
                    "⚠️ SHUTDOWN_SEQUENCE excedió ventana de espera local; saliendo para evitar SIGKILL de systemd."
                )
    except Exception as error:
        logger.critical(f"❌ FATAL ERROR: {error}\n{traceback.format_exc()}")
        sys.exit(1)

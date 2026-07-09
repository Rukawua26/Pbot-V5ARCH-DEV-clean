import os
import threading
import uuid
from collections import deque

from config import Config
from core.config.portable_paths import models_dir
from core.cooldown_state import load_cooldowns
from core.execution_runtime_state import load_execution_runtime_state
from core.providers.global_market import GlobalMarketProvider
from core.time_utils import monotonic_now
from core.ws_reconciliation import handle_ws_reconnected


def init_runtime_state(bot, has_weight_tracker, weight_tracker_cls):
    bot.active_trades = {}
    bot.recent_closed_trades = []
    bot.scanner_history = []
    bot.consensus_history = deque(maxlen=200)
    bot.logs = deque(maxlen=Config.LOG_LIMIT)
    bot.balance = 0.0
    bot.available_balance = 0.0
    bot.pairs_to_scan = []
    bot.is_running = True
    bot._shutdown_event = threading.Event()
    bot.stop_requested = False
    bot.shutdown_in_progress = False
    bot.shutdown_complete = threading.Event()
    bot._shutdown_thread = None
    bot.init_complete = threading.Event()
    bot.startup_error = None
    bot._api_weight_logged = False
    bot.weight_tracker = None
    if has_weight_tracker and weight_tracker_cls is not None:
        bot.weight_tracker = weight_tracker_cls()
    if hasattr(bot.execution, "set_weight_tracker"):
        bot.execution.set_weight_tracker(bot.weight_tracker)
    if hasattr(bot.execution, "set_simulated_balance_provider"):
        bot.execution.set_simulated_balance_provider(
            lambda: float(getattr(bot, "balance", 0.0) or 0.0)
        )
    if hasattr(bot.data_service, "set_weight_tracker"):
        bot.data_service.set_weight_tracker(bot.weight_tracker)

    bot._funding_rate_cache = {}
    bot._funding_cache_ttl = 300
    bot._btc_data_cache = None
    bot._btc_data_cache_ts = 0
    bot._symbol_controls_cache = {
        "blocked": set(),
        "preferred": set(),
        "reduced": set(),
        "loaded_at": 0.0,
    }
    bot._symbol_controls_refresh_interval = int(
        getattr(Config, "SYMBOL_CONTROLS_REFRESH_SECONDS", 1800)
    )
    bot._symbol_controls_last_refresh = 0.0
    bot._symbol_reduced_size_mult = float(getattr(Config, "SYMBOL_REDUCED_SIZE_MULTIPLIER", 0.5))
    bot._exit_eval_last_log = {}
    now_mono = monotonic_now()
    bot._daily_report_next_ts = now_mono + 24 * 3600
    bot.breakout_overrides_today = 0
    bot.markov_decision_stats = {
        "range_breakout_allowed": 0,
        "range_standard_penalty": 0,
        "range_stagnant_veto": 0,
        "trend_boost": 0,
        "stale_capped": 0,
        "missing_or_expired": 0,
    }
    bot._mfe_alert_last_ts = 0.0
    bot.lock = threading.RLock()
    bot.price_lock = threading.Lock()
    bot.db_lock = threading.RLock()
    bot.balance_lock = threading.Lock()
    bot.scanner_lock = threading.Lock()
    bot.consensus_lock = threading.Lock()

    bot.is_hedge_mode = False
    bot.ghost_model = None
    bot.ghost_model_type = "OFF"
    bot.bootstrap_heuristic_mode = False
    bot.scaler = None
    bot.risk_multiplier = 1.0
    bot.blacklist = {}
    bot.cooldown_pairs = {}
    bot.cooldown_deadlines_mono = {}
    bot.restricted_hours = []
    bot.restricted_sectors = []
    bot.restricted_symbols = []
    bot.circuit_breaker_active = False
    bot.daily_drawdown_alert_sent = False
    bot._drawdown_warning_sent = False
    bot._circuit_breaker_alert_sent = False
    bot.pause_time = None
    bot.is_paused = False
    bot.btc_panic = False
    bot.mandatory_train_pending = False
    bot.force_btc_panic = False
    bot.api_status = "🟡 PENDING"
    bot.force_chaos_mode = False
    bot.integrity_lock_active = False
    bot.halt_system_active = False
    bot.ai_status_msg = "INICIANDO..."
    bot.dynamic_offset = 0.0
    bot.peak_pnl = 0.0
    bot.daily_initial_balance = 0.0
    bot.current_target = Config.DAILY_GOALS[0]
    bot.user_notes = "Escribe tus notas aquí..."
    bot.global_rag_impact = 0.0
    bot.instance_uuid = str(uuid.uuid4())[:12]
    bot.pending_send_stale_seconds = int(getattr(Config, "PENDING_SEND_STALE_SECONDS", 30))
    bot.last_entry_open_ts = 0.0
    bot.last_shadow_signal_ts = 0.0
    bot._last_real_auth_healthcheck_mono = 0.0
    bot.confidence_stagnation_lock_active = False


def init_realtime_and_monitoring(
    bot,
    websocket_cls,
    ml_monitor_available,
    ml_monitor_cls,
):
    bot.data_service.load_cache()

    bootstrap_symbols = []
    for symbol in list(getattr(Config, "PAIRS", []) or []):
        if not isinstance(symbol, str):
            continue
        clean = symbol.strip()
        if not clean:
            continue
        bootstrap_symbols.append(clean)

    if not bootstrap_symbols:
        bootstrap_symbols = ["BTC/USDT"]

    bot.ws_manager = websocket_cls(
        symbols=bootstrap_symbols,
        enable_cvd=bool(getattr(Config, "CVD_FILTER_ENABLED", False)),
        cvd_window_seconds=int(getattr(Config, "CVD_WINDOW_SECONDS", 300)),
        on_reconnect=lambda **kwargs: handle_ws_reconnected(bot, **kwargs),
    )
    bot.ws_manager.start_background()

    bot.global_market_provider = GlobalMarketProvider()
    bot.global_market_provider.start()
    bot.global_market_cache = {}

    if ml_monitor_available and ml_monitor_cls is not None:
        bot.ml_monitor = ml_monitor_cls(str(models_dir()))
        bot._init_ml_monitoring()
    else:
        bot.ml_monitor = None

    try:
        restored = bot.brain.load_active_trade_states()
        if restored:
            bot.active_trades = restored
            bot.log(f"💾 Restaurados {len(restored)} trades activos desde DB.")
    except Exception as error:
        bot.log(f"⚠️ Error restaurando trades: {error}")

    try:
        load_cooldowns(bot)
        if bot.cooldown_pairs:
            bot.log(f"❄️ Restaurados {len(bot.cooldown_pairs)} cooldowns persistentes.")
    except Exception as error:
        bot.log(f"⚠️ Error restaurando cooldowns: {error}")

    try:
        load_execution_runtime_state(bot)
    except Exception as error:
        bot.log(f"⚠️ Error restaurando execution runtime state: {error}")

    threading.Thread(target=bot._heartbeat_loop, daemon=True).start()

    bot.cache_dir = "data_storage/candles"
    os.makedirs(bot.cache_dir, exist_ok=True)
    bot.market_btc_price = 0.0
    bot.live_prices = {}
    bot.live_prices_ts = {}
    bot.market_btc_price_source = "INIT"
    bot.market_btc_price_ts = 0.0
    bot.current_sentiment = ("⚪ ANALIZANDO...", "white")
    bot.last_ohlcv_fetch = {}
    bot.last_train_date = bot.brain.get_last_train_timestamp()
    bot.ml_healthy = True
    bot.last_radar_update = monotonic_now()

    bot._weekly_sent = False
    bot._vol_ema = {}
    bot._snapshot_tickers = {}
    bot.last_ml_health_check = monotonic_now()
    bot.last_perf_check = monotonic_now()
    bot.last_panic_alert = 0
    bot.last_ml_confidence = 75.0
    bot.last_ghost_weight = 1.0
    bot.ml_performance = {}
    bot.last_signal_stats = {}
    bot._last_sort_time = 0
    bot.last_market_update = 0
    bot.last_pm_check = 0
    bot.day_report_sent = False
    bot.daily_backup_done = False
    bot.last_cache_save = monotonic_now()
    bot._api_weight_logged_time = monotonic_now()

    bot._perf_start_ts = monotonic_now()
    bot._perf_start_rss_mb = 0.0
    bot._perf_h1_logged = False
    bot._perf_h24_logged = False
    bot._guardian_stats = {
        "loops": 0,
        "work_s": 0.0,
        "sleep_s": 0.0,
        "bailout_count": 0,
    }
    bot._secondary_scan_due_at = 0.0
    bot._last_weekly_maintenance_utc = None
    bot.latency_quarantine = {}

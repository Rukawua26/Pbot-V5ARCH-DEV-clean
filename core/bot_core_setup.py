from config import Config
from core.analytics.fvg_tracker import FvgTracker
from core.candle_close_cache import CandleCloseCache
from core.data_service import DataService
from core.execution_adapters import build_execution_gateway
from core.execution_port import ExecutionPort
from core.execution_service import ExecutionService
from core.intent_deduper import IntentDeduper
from core.risk.exit_engine_v1 import ExitEngineV1
from core.risk_engine import RiskEngine
from core.shadow_validation import emit_config_snapshot
from core.strategy.agents.breakout_agent import BreakoutAgent
from core.strategy.utils import StrategyUtils


def init_core_services_and_engines(bot):
    bot.execution: ExecutionPort = build_execution_gateway(Config, ExecutionService)
    bot.data_service = DataService(bot.execution.exchange)
    bot.risk_engine = RiskEngine(bot.brain)
    bot.crash_predictor = bot.risk_engine.crash_predictor
    bot.intent_deduper = IntentDeduper()
    bot.candle_cache = CandleCloseCache()
    StrategyUtils._candle_cache = bot.candle_cache
    bot.breakout_agent = BreakoutAgent(
        min_ia_prob=float(getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0)),
        volume_multiplier=float(getattr(Config, "BREAKOUT_VOLUME_MULT", 1.5)),
        breakout_buffer_pct=float(getattr(Config, "BREAKOUT_BUFFER_PCT", 0.5)),
        timeout_minutes=int(getattr(Config, "BREAKOUT_TIMEOUT_MINUTES", 60)),
    )
    bot.exit_engine = ExitEngineV1(
        time_decay_bars=int(getattr(Config, "EXIT_TIME_DECAY_BARS", 4)),
        escape_velocity_pct=float(getattr(Config, "EXIT_ESCAPE_VELOCITY_PCT", 0.2)),
        structural_atr_buffer=float(getattr(Config, "EXIT_STRUCTURAL_ATR_BUFFER", 0.25)),
        structural_min_buffer_pct=float(getattr(Config, "EXIT_STRUCTURAL_MIN_BUFFER_PCT", 0.05)),
        structural_min_hold_seconds=int(getattr(Config, "EXIT_STRUCTURAL_MIN_HOLD_SECONDS", 120)),
        trailing_activation_pct=float(getattr(Config, "EXIT_TRAILING_ACTIVATION_PCT", 0.9)),
        trailing_atr_mult=float(getattr(Config, "EXIT_TRAILING_ATR_MULT", 3.0)),
        trailing_atr_mult_tight=float(getattr(Config, "EXIT_TRAILING_ATR_MULT_TIGHT", 1.5)),
        trailing_tighten_pnl_pct=float(getattr(Config, "EXIT_TRAILING_TIGHTEN_PNL_PCT", 2.0)),
        trailing_min_distance_pct=float(getattr(Config, "EXIT_TRAILING_MIN_DISTANCE_PCT", 0.3)),
        breakeven_trigger_pct=float(getattr(Config, "EXIT_BREAKEVEN_TRIGGER_PCT", 1.2)),
        breakeven_atr_mult=float(getattr(Config, "EXIT_BREAKEVEN_ATR_MULT", 1.2)),
        breakeven_lock_pct=float(getattr(Config, "EXIT_BREAKEVEN_LOCK_PCT", 0.1)),
        flat_time_decay_bars=int(getattr(Config, "EXIT_FLAT_TIME_DECAY_BARS", 3)),
        flat_time_decay_atr_mult=float(getattr(Config, "EXIT_FLAT_TIME_DECAY_ATR_MULT", 0.5)),
    )

    if Config.FVG_TRACKER_ENABLED:
        bot.fvg_tracker = FvgTracker(
            enabled=Config.FVG_TRACKER_ENABLED,
            min_gap_pct=Config.FVG_MIN_GAP_PCT,
            max_candles_scan=Config.FVG_MAX_CANDLES_SCAN,
            alert_throttle_seconds=Config.FVG_ALERT_THROTTLE_SEC,
            expiration_bars=Config.FVG_EXPIRATION_BARS,
            telegram_alerts=Config.FVG_ALERT_TELEGRAM,
            max_symbols_per_cycle=Config.FVG_MAX_SYMBOLS_PER_CYCLE,
        )

    emit_config_snapshot()

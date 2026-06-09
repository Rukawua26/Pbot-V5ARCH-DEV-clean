from core.config.env_helpers import (
    _CONFIG_ENV_WARNINGS as _CONFIG_ENV_WARNINGS,
)
from core.config.env_helpers import (
    env_bool as _env_bool,
)
from core.config.env_helpers import (
    env_float as _env_float,
)
from core.config.env_helpers import (
    env_int as _env_int,
)
from core.config.env_helpers import (
    env_list as _env_list,
)
from core.config.env_helpers import (
    get_env_warnings,
)
from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig
from core.symbol_utils import normalize_position_symbol


class Config(OperationalConfig, StrategyConfig):
    """
    Clase de configuración unificada.
    Hereda de Operational y Strategy para mantener la compatibilidad con el resto del código.
    """

    MAX_SPREAD_THRESHOLD = _env_float("MAX_SPREAD_THRESHOLD", 0.008)
    MAX_SLIPPAGE = _env_float("MAX_SLIPPAGE", 0.001)
    VIRTUAL_FEE = _env_float("VIRTUAL_FEE", 0.001)
    ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS = _env_float("ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS", 2.0)
    BTC_RISK_MAX_PRICE_AGE_SECONDS = _env_float("BTC_RISK_MAX_PRICE_AGE_SECONDS", 90.0)
    HALT_RECOVERY_MAX_ATTEMPTS = _env_int("HALT_RECOVERY_MAX_ATTEMPTS", 5)

    # --- Risk overrides ---
    RISK_PER_TRADE_PERCENT = _env_float("RISK_PER_TRADE_PERCENT", 1.2)
    RISK_PER_TRADE = RISK_PER_TRADE_PERCENT
    RISK_PER_TRADE_PCT = RISK_PER_TRADE_PERCENT / 100.0
    MAX_RISK_USD = _env_float("MAX_RISK_USD", 2.0)
    MAX_OPEN_TRADES = _env_int("MAX_OPEN_TRADES", 3)
    MAX_DIRECTIONAL_TRADES = _env_int("MAX_DIRECTIONAL_TRADES", 2)
    MIN_NOTIONAL_VALUE = _env_float("MIN_NOTIONAL_VALUE", 5.0)
    MAX_MARGIN_PERCENT = _env_float("MAX_MARGIN_PERCENT", 5.0)
    DAILY_LOSS_LIMIT = _env_float("DAILY_LOSS_LIMIT", 2.0)

    # --- ML weight overrides ---
    XGB_WEIGHT = _env_float("XGB_WEIGHT", 0.30)
    LGB_WEIGHT = _env_float("LGB_WEIGHT", 0.30)
    RF_WEIGHT = _env_float("RF_WEIGHT", 0.20)
    GB_WEIGHT = _env_float("GB_WEIGHT", 0.15)
    LR_WEIGHT = _env_float("LR_WEIGHT", 0.05)

    # --- Umbrales de operación 1H (fase final) ---
    REAL_MODE_THRESHOLD = _env_float("REAL_MODE_THRESHOLD", 70.0)
    SHADOW_MODE_MIN = _env_float("SHADOW_MODE_MIN", 55.0)
    SHADOW_MODE_MAX = _env_float("SHADOW_MODE_MAX", 69.9)

    # --- ABLATION STUDY PROFILES (Fase 1) ---
    # El Baseline es: Sizing fijo, SL/TP estático (ATR), sin IA, sin filtros.
    ABLATION_PROFILES = {
        "BASELINE": {
            "EXIT_ENGINE_V1_ENABLED": False,
            "HMM_REGIME_ENABLED": False,
            "OI_FILTER_ENABLED": False,
            "CVD_FILTER_ENABLED": False,
            "MTF_FILTER_ENABLED": False,
            "CORRELATION_RISK_ENABLED": False,
            "REGIME_TUNING_ENABLED": False,
            "RAG_ENABLED": False,
            "USE_KELLY_SIZING": False,  # Usará calculate_position_size_by_stop
        },
        "FULL_INSTITUTIONAL": {
            # Todos los flags en True (estado actual)
        },
    }

    # --- HMM/Markov regime probability controls ---
    MARKOV_BREAKOUT_MIN = _env_float("MARKOV_BREAKOUT_MIN", 75.0)
    MARKOV_DEAD_ZONE_MAX = _env_float("MARKOV_DEAD_ZONE_MAX", 30.0)
    MARKOV_RANGE_BREAKOUT_WEIGHT = _env_float("MARKOV_RANGE_BREAKOUT_WEIGHT", 0.90)
    MARKOV_RANGE_STANDARD_WEIGHT = _env_float("MARKOV_RANGE_STANDARD_WEIGHT", 0.75)
    MARKOV_BULL_STRONG_WEIGHT = _env_float("MARKOV_BULL_STRONG_WEIGHT", 1.10)
    MARKOV_BEAR_STRONG_WEIGHT = _env_float("MARKOV_BEAR_STRONG_WEIGHT", 1.10)
    MARKOV_SNAPSHOT_MAX_AGE_SECONDS = _env_float("MARKOV_SNAPSHOT_MAX_AGE_SECONDS", 2 * 60 * 60)
    MARKOV_SNAPSHOT_STALE_SECONDS = _env_float("MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 60 * 60)
    MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS = _env_float(
        "MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS", 5 * 60
    )
    MARKOV_PREVETO_BEARISH_REVERSAL_MIN = _env_float("MARKOV_PREVETO_BEARISH_REVERSAL_MIN", 85.0)
    BEAR_COUNTER_WEIGHT = _env_float("BEAR_COUNTER_WEIGHT", 0.70)

    # --- BEAR_TREND pair universe reduction ---
    BEAR_TREND_MAX_PAIRS = _env_int("BEAR_TREND_MAX_PAIRS", 15)
    BEAR_TREND_CONFIDENCE_BOOST = _env_float("BEAR_TREND_CONFIDENCE_BOOST", 10.0)

    # --- Mapa de SHOCKS (filtro de espacio operativo) ---
    SHOCK_MIN_DIST_PCT = _env_float("SHOCK_MIN_DIST_PCT", 0.2)
    HMM_RANGE_PENALTY = _env_float("HMM_RANGE_PENALTY", 0.80)
    HMM_RANGE_VETO = _env_bool("HMM_RANGE_VETO", True)
    SHOCK_PIVOT_WINDOW = _env_int("SHOCK_PIVOT_WINDOW", 3)
    SHOCK_LOOKBACK_BARS = _env_int("SHOCK_LOOKBACK_BARS", 240)

    # --- Breakout Hunter (pasivo) ---
    BREAKOUT_WATCH_ENABLED = _env_bool("BREAKOUT_WATCH_ENABLED", True)
    BREAKOUT_MIN_IA_PROB = _env_float("BREAKOUT_MIN_IA_PROB", 55.0)
    BREAKOUT_SHOCK_MIN_IA_PROB = _env_float("BREAKOUT_SHOCK_MIN_IA_PROB", 50.0)
    BREAKOUT_WATCH_COHERENCE_ENABLED = _env_bool("BREAKOUT_WATCH_COHERENCE_ENABLED", True)
    BREAKOUT_COHERENCE_MIN_IA_PROB = _env_float("BREAKOUT_COHERENCE_MIN_IA_PROB", 50.0)
    BREAKOUT_BUFFER_PCT = _env_float("BREAKOUT_BUFFER_PCT", 0.5)
    BREAKOUT_VOLUME_MULT = _env_float("BREAKOUT_VOLUME_MULT", 1.5)
    BREAKOUT_TIMEOUT_MINUTES = _env_int("BREAKOUT_TIMEOUT_MINUTES", 60)
    BREAKOUT_SEMI_ACTIVE_SHADOW = _env_bool("BREAKOUT_SEMI_ACTIVE_SHADOW", True)
    BREAKOUT_EXTREME_IA_PROB = _env_float("BREAKOUT_EXTREME_IA_PROB", 75.0)
    DIRECTIONAL_COHERENCE_FILTER = _env_bool("DIRECTIONAL_COHERENCE_FILTER", True)

    # --- Open Interest Delta Filter (v118.3) ---
    OI_FILTER_ENABLED = _env_bool("OI_FILTER_ENABLED", True)
    OI_DELTA_THRESHOLD = _env_float("OI_DELTA_THRESHOLD", 0.005)
    OI_CACHE_TTL_SECONDS = _env_int("OI_CACHE_TTL_SECONDS", 180)
    SIGNAL_ANALYSIS_WORKERS = _env_int("SIGNAL_ANALYSIS_WORKERS", 1)

    # --- CVD / Order Flow Filter (Fase 12.3) ---
    CVD_FILTER_ENABLED = _env_bool("CVD_FILTER_ENABLED", False)
    CVD_WINDOW_SECONDS = _env_int("CVD_WINDOW_SECONDS", 300)
    CVD_IMBALANCE_THRESHOLD = _env_float("CVD_IMBALANCE_THRESHOLD", 0.12)
    CVD_MIN_QUOTE_VOLUME = _env_float("CVD_MIN_QUOTE_VOLUME", 1000.0)
    CVD_ALIGNED_WEIGHT = _env_float("CVD_ALIGNED_WEIGHT", 1.05)
    CVD_CONFLICT_WEIGHT = _env_float("CVD_CONFLICT_WEIGHT", 0.85)

    # --- Multi-timeframe signal confirmation ---
    MTF_FILTER_ENABLED = _env_bool("MTF_FILTER_ENABLED", False)
    MTF_DIRECTION_WINDOW = _env_int("MTF_DIRECTION_WINDOW", 20)
    MTF_DIRECTION_THRESHOLD_PCT = _env_float("MTF_DIRECTION_THRESHOLD_PCT", 0.002)
    MTF_ALIGNED_BOOST = _env_float("MTF_ALIGNED_BOOST", 1.0)

    # --- Agent Weight Decay Monitor ---
    AGENT_DEGRADATION_THRESHOLD = _env_float("AGENT_DEGRADATION_THRESHOLD", 0.85)
    AGENT_MONITOR_WINDOW = _env_int("AGENT_MONITOR_WINDOW", 20)
    AGENT_MIN_TRADES_BEFORE_ALERT = _env_int("AGENT_MIN_TRADES_BEFORE_ALERT", 10)
    AGENT_MONITOR_INTERVAL_MINUTES = _env_int("AGENT_MONITOR_INTERVAL_MINUTES", 60)

    # --- MTF Metrics ---
    MTF_METRICS_WINDOW = _env_int("MTF_METRICS_WINDOW", 100)

    # --- Correlation Risk (Fase 12.1) ---
    CORRELATION_RISK_ENABLED = _env_bool("CORRELATION_RISK_ENABLED", False)
    CORRELATION_RISK_THRESHOLD = _env_float("CORRELATION_RISK_THRESHOLD", 0.85)
    CORRELATION_RISK_REDUCTION_MAX = _env_float("CORRELATION_RISK_REDUCTION_MAX", 0.50)
    CORRELATION_RISK_WINDOW = _env_int("CORRELATION_RISK_WINDOW", 48)
    CORRELATION_RISK_MIN_CANDLES = _env_int("CORRELATION_RISK_MIN_CANDLES", 24)

    # --- Regime Auto-Tuning (Fase 12.2) ---
    REGIME_TUNING_ENABLED = _env_bool("REGIME_TUNING_ENABLED", True)
    REGIME_TUNING_MIN_TRADES = _env_int("REGIME_TUNING_MIN_TRADES", 20)
    REGIME_TUNING_SL_RANGE_MIN = _env_float("REGIME_TUNING_SL_RANGE_MIN", 0.60)
    REGIME_TUNING_SL_RANGE_MAX = _env_float("REGIME_TUNING_SL_RANGE_MAX", 1.20)
    REGIME_TUNING_TP_RANGE_MIN = _env_float("REGIME_TUNING_TP_RANGE_MIN", 0.70)
    REGIME_TUNING_TP_RANGE_MAX = _env_float("REGIME_TUNING_TP_RANGE_MAX", 1.30)

    # --- Exit Engine v118 (dinámico) ---
    EXIT_ENGINE_V1_ENABLED = _env_bool("EXIT_ENGINE_V1_ENABLED", True)
    EXIT_TIME_DECAY_BARS = _env_int("EXIT_TIME_DECAY_BARS", 4)
    EXIT_ESCAPE_VELOCITY_PCT = _env_float("EXIT_ESCAPE_VELOCITY_PCT", 0.2)
    EXIT_STRUCTURAL_ATR_BUFFER = _env_float("EXIT_STRUCTURAL_ATR_BUFFER", 0.25)
    EXIT_STRUCTURAL_MIN_BUFFER_PCT = _env_float("EXIT_STRUCTURAL_MIN_BUFFER_PCT", 0.05)
    EXIT_STRUCTURAL_MIN_HOLD_SECONDS = _env_int("EXIT_STRUCTURAL_MIN_HOLD_SECONDS", 120)
    EXIT_TRAILING_ACTIVATION_PCT = _env_float("EXIT_TRAILING_ACTIVATION_PCT", 0.9)
    EXIT_TRAILING_ATR_MULT = _env_float("EXIT_TRAILING_ATR_MULT", 3.0)
    EXIT_TRAILING_ATR_MULT_TIGHT = _env_float("EXIT_TRAILING_ATR_MULT_TIGHT", 1.5)
    EXIT_TRAILING_TIGHTEN_PNL_PCT = _env_float("EXIT_TRAILING_TIGHTEN_PNL_PCT", 2.0)
    EXIT_TRAILING_MIN_DISTANCE_PCT = _env_float("EXIT_TRAILING_MIN_DISTANCE_PCT", 0.3)
    EXIT_BREAKEVEN_TRIGGER_PCT = _env_float("EXIT_BREAKEVEN_TRIGGER_PCT", 1.2)
    EXIT_BREAKEVEN_ATR_MULT = _env_float("EXIT_BREAKEVEN_ATR_MULT", 1.2)
    EXIT_BREAKEVEN_LOCK_PCT = _env_float("EXIT_BREAKEVEN_LOCK_PCT", 0.1)
    EXIT_FLAT_TIME_DECAY_BARS = _env_int("EXIT_FLAT_TIME_DECAY_BARS", 3)
    EXIT_FLAT_TIME_DECAY_ATR_MULT = _env_float("EXIT_FLAT_TIME_DECAY_ATR_MULT", 0.5)

    # Compatibilidad con rutas actuales de decisión (0-1)
    REAL_CONFIDENCE_MIN = REAL_MODE_THRESHOLD / 100.0
    REAL_CONFIDENCE_THRESHOLD = REAL_CONFIDENCE_MIN
    SHADOW_PROB_MIN = SHADOW_MODE_MIN / 100.0

    @staticmethod
    def sanitize_symbol(sym: str) -> str:
        """Normalización estricta a SYMBOL/USDT."""
        return normalize_position_symbol(sym, default_quote="USDT", strict=True)

    SYMBOL_BLACKLIST = _env_list(
        "SYMBOL_BLACKLIST",
        [
            "PUMP/USDT",
            "KITE/USDT",
            "STABLE/USDT",
            "BERA/USDT",
            "WIF/USDT",
            "ZAMA/USDT",
            "AGLD/USDT",
            "MATIC/USDT",
            "EOS/USDT",
            "FARTCOIN/USDT",
        ],
    )

    @classmethod
    def sanitize_pairs(cls, pairs: list) -> list:
        """Sanitizador de símbolos deduplicado."""
        sanitized = []
        for p in pairs:
            cleaned = cls.sanitize_symbol(p)
            if cleaned and cleaned.endswith("/USDT"):
                sanitized.append(cleaned)
        return list(dict.fromkeys(sanitized))

    @classmethod
    def env_warnings(cls) -> list[str]:
        return get_env_warnings()

    @classmethod
    def validate(cls) -> list[str]:
        errors = []

        # --- Guardrails de seguridad obligatorios ---
        if not cls.PAPER_MODE and not cls.ALLOW_REAL_TRADING:
            errors.append(
                "REQUIERE_ALLOW_REAL_TRADING: modo REAL requiere ALLOW_REAL_TRADING=true explícito. "
                "Esto evita activación accidental de trading con capital real."
            )

        if not cls.PAPER_MODE:
            if not cls.BINANCE_API_KEY or not cls.BINANCE_API_SECRET:
                errors.append(
                    "REAL_MODE_SIN_KEYS: modo REAL requiere BINANCE_API_KEY y BINANCE_API_SECRET."
                )
            if cls.MAX_OPEN_TRADES < 1 or cls.MAX_OPEN_TRADES > 5:
                errors.append(
                    "REAL_MODE_MAX_TRADES: en modo REAL, MAX_OPEN_TRADES debe estar entre 1 y 5."
                )
            if cls.MAX_RISK_USD <= 0 or cls.MAX_RISK_USD > 100:
                errors.append(
                    "REAL_MODE_MAX_RISK: en modo REAL, MAX_RISK_USD debe estar entre 0 y 100."
                )
            if float(cls.RISK_PER_TRADE_PERCENT) <= 0 or float(cls.RISK_PER_TRADE_PERCENT) > 2.0:
                errors.append(
                    "REAL_MODE_RISK_PCT: en modo REAL, RISK_PER_TRADE_PERCENT debe estar entre 0% y 2%."
                )
            if not cls.TELEGRAM_TOKEN or not cls.TELEGRAM_CHAT_ID:
                errors.append(
                    "REAL_MODE_TELEGRAM: modo REAL requiere TELEGRAM_TOKEN y TELEGRAM_CHAT_ID."
                )

        if not (0.0 < float(cls.RISK_PER_TRADE_PERCENT) <= 5.0):
            errors.append("RISK_PER_TRADE_PERCENT debe estar en (0, 5]")
        if int(cls.MAX_OPEN_TRADES) < 0 or int(cls.MAX_OPEN_TRADES) > 20:
            errors.append("MAX_OPEN_TRADES debe estar entre 0 y 20")
        if int(cls.MAX_DIRECTIONAL_TRADES) < 0 or int(cls.MAX_DIRECTIONAL_TRADES) > int(
            cls.MAX_OPEN_TRADES
        ):
            errors.append("MAX_DIRECTIONAL_TRADES debe estar entre 0 y MAX_OPEN_TRADES")
        if float(cls.SHADOW_MODE_MIN) >= float(cls.REAL_MODE_THRESHOLD):
            errors.append("SHADOW_MODE_MIN debe ser menor que REAL_MODE_THRESHOLD")
        if float(cls.SHADOW_MODE_MAX) < float(cls.SHADOW_MODE_MIN):
            errors.append("SHADOW_MODE_MAX debe ser >= SHADOW_MODE_MIN")
        if float(cls.MAX_SLIPPAGE) < 0 or float(cls.MAX_SLIPPAGE) > 0.05:
            errors.append("MAX_SLIPPAGE debe estar entre 0 y 0.05")
        if float(cls.BTC_RISK_MAX_PRICE_AGE_SECONDS) <= 0:
            errors.append("BTC_RISK_MAX_PRICE_AGE_SECONDS debe ser positivo")
        if int(cls.HALT_RECOVERY_MAX_ATTEMPTS) < 1:
            errors.append("HALT_RECOVERY_MAX_ATTEMPTS debe ser >= 1")

        total_weight = (
            cls.XGB_WEIGHT + cls.LGB_WEIGHT + cls.RF_WEIGHT + cls.GB_WEIGHT + cls.LR_WEIGHT
        )
        if not (0.99 <= float(total_weight) <= 1.01):
            errors.append("La suma de pesos ML debe estar cerca de 1.0")
        return errors

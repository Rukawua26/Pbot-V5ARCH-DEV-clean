import os


class StrategyConfig:
    """
    Configuración de trading, indicadores y lógica de mercado.

    NOTA: Las variables definidas AQUÍ son DEFAULTS estáticos.
    Si también existen en core/config/manager.py con _env_*(),
    el valor efectivo lo determina manager.py (env var > default de manager > herencia).
    Los duplicados se mantienen solo como documentación legacy — manager.py prevalece.
    """

    TIMEFRAME = "1h"

    # --- GESTIÓN DE RIESGO (solo únicos aquí; manager.py tiene los env-overridables) ---
    MIN_NOTIONAL_VALUE = 5.0  # manager.py lo sobreescribe con env var
    MAX_NOTIONAL_MULTIPLIER = 5.0
    MAX_SHADOW_TRADES = int(os.getenv("MAX_SHADOW_TRADES", "20"))
    LEVERAGE = 10
    IS_DEBUG = False

    TRADE_COOLDOWN_MINUTES = 60
    SHADOW_COOLDOWN_MINUTES = 5
    SYMBOL_COOLDOWN_MINUTES = 30
    SMART_EXIT_COOLDOWN_HOURS = 4
    MAX_TRADE_DURATION_MINUTES = 60

    MAX_DAILY_DRAWDOWN_PCT = 0.03
    DAILY_TRAILING_STOP = 3.0
    DAILY_GOALS = [5.0, 10.0, 15.0]
    DAILY_LOSS_LIMIT_FORCE_PAPER = -3.0

    # --- UMBRALES TÉCNICOS ---
    NATR_THRESHOLD = 2.0
    MIN_VOLUME_24H = 500_000
    MAX_SECTOR_EXPOSURE = 3
    ADX_TREND_THRESHOLD = 17
    MIN_VOL_REL = 0.1
    MARKET_BREADTH_FEAR_THRESHOLD = 0.70
    MARKET_BREADTH_GREED_THRESHOLD = 0.70
    HMM_REGIME_ENABLED = True
    HMM_LOOKBACK_CANDLES = 336
    HMM_BOOTSTRAP_CANDLES = 1000
    HMM_RETRAIN_INTERVAL_SECONDS = 4 * 60 * 60
    HMM_MIN_CONFIDENCE = 0.55
    HMM_RANGE_VETO = True

    # --- Hurst exponent (manager.py prevalece por MRO) ---
    HURST_ENABLED = True
    HURST_WINDOW = 128
    HURST_MAX_LAG = 64
    HURST_MIN_LAG = 10
    HURST_PERSISTENT_THRESHOLD = 0.55
    HURST_ANTIPERSISTENT_THRESHOLD = 0.45
    HURST_MT_BOOST = 0.10
    HURST_SR_BOOST = 0.10
    HURST_RANDOM_PENALTY = 0.95
    HURST_ALIGNED_BOOST = 1.05
    HURST_COUNTER_PENALTY = 0.90

    # Nota: HMM_RANGE_PENALTY está en manager.py como env (default 0.80).
    # El valor 0.5 aquí es legacy — manager.py prevalece por MRO.

    WS_TICKER_MAX_AGE_SECONDS = 15.0

    EMA_9 = 9
    EMA_21 = 21
    EMA_50 = 50
    EMA_200 = 200

    # --- STOP LOSS Y TAKE PROFIT (ATR DINÁMICO v119) ---
    ATR_SL_MULTIPLIER_RANGE = 0.2
    ATR_SL_MULTIPLIER_TREND = 1.0
    ATR_TP_MULTIPLIER_RANGE = 1.2
    ATR_TP_MULTIPLIER_TREND = 3.0

    TRAILING_ACTIVATION_PNL = 1.20
    EARLY_BREAKEVEN_ACTIVATION_PNL = 1.5
    TRAILING_BREAKEVEN_PNL = 3.0
    TRAILING_BREAKEVEN_PULLBACK = 2.0
    TRAILING_ATR_MULTIPLIER = 2.5

    DYNAMIC_SL_TP = True
    ATR_TP1_MULTIPLIER = 2.0
    ATR_TP2_MULTIPLIER = 4.0

    TP1_ENABLED = False
    TP1_PERCENT = 50
    TP1_LEVEL = 1.8
    TP2_ENABLED = False
    TP2_PERCENT = 50
    TP2_LEVEL = 3.6

    MIN_TP_NET_PERCENT = 0.5
    MIN_TP_SPREAD_MULTIPLIER = 2.0

    # --- FILTROS DE ENTRADA ---
    REAL_RSI_BUY = 30
    REAL_RSI_SELL = 70
    SHADOW_RSI_BUY = 35
    SHADOW_RSI_SELL = 65

    ENTRY_RSI_MIN = 45.0
    ENTRY_RSI_MAX = 68.0
    STOCH_REAL_BUY_THRESHOLD = 20
    STOCH_REAL_SELL_THRESHOLD = 70
    STOCH_SHADOW_BUY_THRESHOLD = 30
    STOCH_SHADOW_SELL_THRESHOLD = 70

    # --- BLACKLIST (manager.py la sobreescribe con env var) ---
    SYMBOL_BLACKLIST = [
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
    ]

    # --- ML & PESOS ---
    SMART_AGENT_WEIGHTS = True
    CONTEXTUAL_WEIGHTING = True

    # --- CRASH PREDICTOR ---
    CRASH_DETECTION_ENABLED = True
    CRASH_PROBABILITY_THRESHOLD = 50
    CRASH_EMERGENCY_THRESHOLD = 70
    CRASH_BTC_DROP_THRESHOLD = 1.5
    CRASH_FUNDING_THRESHOLD = 0.03

    # --- RAG ---
    RAG_ENABLED = True
    RAG_SIMILARITY_THRESHOLD = 0.85
    RAG_MIN_MATCHES = 3

    # --- PARÁMETROS DE PÁNICO ---
    BTC_PANIC_DROP_PERCENT = 1.5
    REAL_HARD_SL_PERCENT = -3.0
    SHADOW_HARD_SL_PERCENT = -5.0
    SHADOW_MIN_PROBABILITY_TREND = 0.65
    SHADOW_MIN_PROBABILITY_RANGE = 0.70
    IA_SHADOW_THRESHOLD = 0.65

    TRAIL_ENTRY_OFFSET = 0.0005
    TRAIL_AFTER_TP = True
    TRAIL_AFTER_TP1 = TRAIL_AFTER_TP
    PRICE_PRIORITY_LIMIT = 0.001
    ML_HEALTH_MIN_ACCURACY = 0.40
    ML_HEALTH_VETO_ENABLED = True
    MAX_MARGIN_PERCENT = 5.0  # manager.py lo sobreescribe con env var

    # --- PRIORIZACIÓN DE RADAR ---
    RADAR_PRIORITY_HIGH_WR = 1.0
    RADAR_PRIORITY_HIGH_VOL_LOW_PRICE = 0.8
    RADAR_PRIORITY_OTHERS = 0.5

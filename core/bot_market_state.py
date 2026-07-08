import logging
import threading
import time
from datetime import UTC, datetime

import pandas as pd

from config import Config

logger = logging.getLogger("SniperAI")
from core.strategy.hurst import HurstEstimator
from core.strategy.regime_hmm import DynamicHMMRegime

hmm_filter = DynamicHMMRegime(
    n_states=3,
    lookback_candles=int(getattr(Config, "HMM_LOOKBACK_CANDLES", 336)),
)
hurst_estimator = HurstEstimator(
    window=int(getattr(Config, "HURST_WINDOW", 128)),
    max_lag=int(getattr(Config, "HURST_MAX_LAG", 64)),
    min_lag=int(getattr(Config, "HURST_MIN_LAG", 10)),
)
_hurst_cached_value: float | None = None
_last_hmm_retrain_ts = 0.0
_hmm_retrain_lock = threading.Lock()
_hmm_retrain_in_progress = False
_last_hmm_snapshot_persist_ts = None
_last_hmm_snapshot_persist_monotonic = 0.0
_hmm_snapshot_persist_lock = threading.Lock()
_HMM_MARKOV_META_KEY = "hmm_markov_snapshot"


def _snapshot_age_seconds(snapshot) -> float:
    try:
        ts_raw = snapshot.get("ts") if isinstance(snapshot, dict) else None
        if not ts_raw:
            return float("inf")
        parsed = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - parsed).total_seconds())
    except Exception:
        return float("inf")


def _persist_hmm_snapshot_async(bot, snapshot) -> None:
    global _last_hmm_snapshot_persist_ts, _last_hmm_snapshot_persist_monotonic
    if not isinstance(snapshot, dict) or not snapshot.get("is_ready"):
        return

    brain = getattr(bot, "brain", None)
    if brain is None or not hasattr(brain, "set_metadata_json"):
        return

    snapshot_ts = snapshot.get("ts")
    with _hmm_snapshot_persist_lock:
        now = time.monotonic()
        min_interval = float(getattr(Config, "MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS", 5 * 60))
        if snapshot_ts == _last_hmm_snapshot_persist_ts:
            return
        if now - _last_hmm_snapshot_persist_monotonic < min_interval:
            return
        _last_hmm_snapshot_persist_ts = snapshot_ts
        _last_hmm_snapshot_persist_monotonic = now

    def _run_persist():
        try:
            db_lock = getattr(bot, "db_lock", None)
            if db_lock is not None:
                with db_lock:
                    brain.set_metadata_json(_HMM_MARKOV_META_KEY, snapshot)
            else:
                brain.set_metadata_json(_HMM_MARKOV_META_KEY, snapshot)
        except Exception as error:
            log = getattr(bot, "log", None)
            if callable(log):
                log(f"⚠️ No se pudo persistir snapshot HMM Markov: {error}")

    threading.Thread(
        target=_run_persist,
        daemon=True,
        name="hmm-markov-snapshot-persist",
    ).start()


def _load_persisted_hmm_snapshot_if_needed(bot) -> None:
    if hasattr(bot, "hmm_markov_snapshot"):
        return
    brain = getattr(bot, "brain", None)
    if brain is None or not hasattr(brain, "get_metadata_json"):
        return
    try:
        snapshot = brain.get_metadata_json(_HMM_MARKOV_META_KEY, default=None)
        max_age = float(getattr(Config, "MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 60 * 60))
        if isinstance(snapshot, dict) and _snapshot_age_seconds(snapshot) <= max_age:
            bot.hmm_markov_snapshot = snapshot
    except Exception:
        return


def _publish_hmm_snapshot(bot, snapshot) -> None:
    if not isinstance(snapshot, dict):
        return
    bot.hmm_markov_snapshot = dict(snapshot)
    _persist_hmm_snapshot_async(bot, bot.hmm_markov_snapshot)


def _get_cached_btc_1h(bot):
    data_service = getattr(bot, "data_service", None)
    data_cache = getattr(data_service, "data_cache", None)
    if not isinstance(data_cache, dict):
        return None

    cached = data_cache.get("BTC/USDT_1h")
    if cached is None or getattr(cached, "empty", True):
        return None
    if len(cached) < int(getattr(Config, "MIN_CANDLE_HISTORY", 200)):
        return None
    return cached


def warmup_hmm_regime(bot) -> bool:
    """Entrena el HMM durante bootstrap para evitar ceguera heurística inicial."""
    if not bool(getattr(Config, "HMM_REGIME_ENABLED", True)):
        return False
    if hmm_filter.is_ready:
        return True

    try:
        data_service = getattr(bot, "data_service", None)
        exchange = getattr(data_service, "exchange", None)
        if data_service is None or exchange is None:
            bot.log("⚠️ HMM warmup omitido: data_service no disponible")
            return False

        limit = int(getattr(Config, "HMM_BOOTSTRAP_CANDLES", 1000))
        ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=limit)
        if hasattr(data_service, "_track_api_weight"):
            data_service._track_api_weight("fetch_ohlcv", 1, "market")
        if not ohlcv:
            bot.log("⚠️ HMM warmup omitido: BTC/USDT sin velas")
            return False

        columns = ["time", "open", "high", "low", "close", "volume"]
        btc_data = pd.DataFrame(ohlcv, columns=columns)
        if hasattr(data_service, "_clean_df"):
            btc_data = data_service._clean_df(btc_data)
        if len(btc_data) < int(getattr(Config, "HMM_LOOKBACK_CANDLES", 336)):
            bot.log(f"⚠️ HMM warmup omitido: solo {len(btc_data)} velas BTC/USDT")
            return False

        cache_key = "BTC/USDT_1h"
        if hasattr(data_service, "data_cache"):
            data_service.data_cache[cache_key] = btc_data.tail(limit).copy()
        if hasattr(data_service, "last_ohlcv_fetch"):
            data_service.last_ohlcv_fetch[cache_key] = time.time()

        global _last_hmm_retrain_ts
        if hmm_filter.dynamic_retrain(btc_data):
            _last_hmm_retrain_ts = time.monotonic()
            bot.log(f"✅ HMM regime warmup listo con {len(btc_data)} velas BTC/USDT")
            return True

        reason = getattr(hmm_filter, "last_error", "desconocido")
        bot.log(f"⚠️ HMM warmup fallback: {reason}")
        return False
    except Exception as error:
        bot.log(f"⚠️ HMM warmup fallback: {error}")
        return False


def _schedule_hmm_retrain(bot, btc_data, now) -> bool:
    global _hmm_retrain_in_progress

    with _hmm_retrain_lock:
        if _hmm_retrain_in_progress:
            return False
        _hmm_retrain_in_progress = True

    retrain_data = btc_data.copy()

    def _run_retrain():
        global _last_hmm_retrain_ts, _hmm_retrain_in_progress
        try:
            if hmm_filter.dynamic_retrain(retrain_data):
                _last_hmm_retrain_ts = now
            else:
                reason = getattr(hmm_filter, "last_error", "desconocido")
                bot.log(f"⚠️ HMM regime fallback: {reason}")
        finally:
            with _hmm_retrain_lock:
                _hmm_retrain_in_progress = False

    threading.Thread(
        target=_run_retrain,
        daemon=True,
        name="hmm-regime-retrain",
    ).start()
    return True


def _detect_market_regime_heuristic(bot, btc_data=None) -> str:
    try:
        if not hasattr(bot, "market_btc_price") or bot.market_btc_price == 0:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"

        if btc_data is None:
            btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        if btc_data is None or len(btc_data) < 200:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"

        close = btc_data["close"]
        ema_200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        adx_values = btc_data.get("adx")
        if adx_values is None or len(adx_values) < 14:
            from tools.pandas_ta import adx

            btc_data = adx(btc_data["high"], btc_data["low"], btc_data["close"], length=14)
            adx_values = btc_data.get("ADX_14")

        if adx_values is None or len(adx_values) < 14:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"

        adx = adx_values.iloc[-1]
        btc_price = bot.market_btc_price

        if adx < float(getattr(Config, "ADX_TREND_THRESHOLD", 20)):
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"
        if btc_price > ema_200:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "BULL_TREND"
        bot.market_regime_source = "HEURISTIC"
        bot.market_regime_confidence = None
        return "BEAR_TREND"
    except Exception as error:
        bot.log(f"⚠️ Error detecting market regime: {error}")
        bot.market_regime_source = "HEURISTIC_ERROR"
        bot.market_regime_confidence = None
        return "RANGE"


def warmup_hurst(bot) -> bool:
    """Precompute Hurst exponent for BTC during bootstrap."""
    global _hurst_cached_value
    if not bool(getattr(Config, "HURST_ENABLED", True)):
        _hurst_cached_value = None
        return False
    if _hurst_cached_value is not None:
        return True

    try:
        data_service = getattr(bot, "data_service", None)
        if data_service is None:
            return False
        exchange = getattr(data_service, "exchange", None)
        if exchange is None:
            return False

        btc_data = _get_cached_btc_1h(bot)
        if btc_data is None or len(btc_data) < hurst_estimator.window:
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=hurst_estimator.window * 2)
            if hasattr(data_service, "_track_api_weight"):
                data_service._track_api_weight("fetch_ohlcv", 1, "market")
            if not ohlcv:
                return False
            columns = ["time", "open", "high", "low", "close", "volume"]
            btc_data = pd.DataFrame(ohlcv, columns=columns)
            if hasattr(data_service, "_clean_df"):
                btc_data = data_service._clean_df(btc_data)

        if btc_data is None or len(btc_data) < hurst_estimator.window:
            return False

        h = hurst_estimator.compute_from_df(btc_data)
        _hurst_cached_value = h
        bot.hurst_value = h
        bot.hurst_classification = HurstEstimator.classify(h)
        bot.log(
            f"✅ Hurst warmup: H={h:.4f} ({bot.hurst_classification}) "
            f"confianza={HurstEstimator.confidence(h):.2f}"
        )
        return True
    except Exception as error:
        bot.log(f"⚠️ Hurst warmup fallback: {error}")
        _hurst_cached_value = None
        return False


def _compute_hurst_on_btc(bot, btc_data) -> float | None:
    global _hurst_cached_value
    if not bool(getattr(Config, "HURST_ENABLED", True)):
        return None
    try:
        if btc_data is not None and len(btc_data) >= hurst_estimator.window:
            h = hurst_estimator.compute_from_df(btc_data)
            _hurst_cached_value = h
            bot.hurst_value = h
            bot.hurst_classification = HurstEstimator.classify(h)
            return h
    except Exception as error:
        logger.debug("Hurst compute error (non-critical): %s", error)
    return _hurst_cached_value


def _ensure_btc_data(bot):
    btc_data = _get_cached_btc_1h(bot)
    if btc_data is None:
        try:
            btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        except Exception:
            return None
    return btc_data


def detect_market_regime(bot) -> str:
    _load_persisted_hmm_snapshot_if_needed(bot)

    if not bool(getattr(Config, "HMM_REGIME_ENABLED", True)):
        regime = _detect_market_regime_heuristic(bot)
        bot.market_regime = regime
        _compute_hurst_on_btc(bot, _get_cached_btc_1h(bot))
        return regime

    btc_data = None
    try:
        btc_data = _ensure_btc_data(bot)
        if btc_data is None or len(btc_data) < 200:
            regime = _detect_market_regime_heuristic(bot, btc_data)
            bot.market_regime = regime
            _compute_hurst_on_btc(bot, btc_data)
            return regime

        global _last_hmm_retrain_ts
        now = time.monotonic()
        interval = float(getattr(Config, "HMM_RETRAIN_INTERVAL_SECONDS", 4 * 60 * 60))
        if not hmm_filter.is_ready or now - _last_hmm_retrain_ts >= interval:
            scheduled = _schedule_hmm_retrain(bot, btc_data, now)
            if not hmm_filter.is_ready:
                if scheduled:
                    bot.log("⚠️ HMM regime fallback: reentrenamiento en progreso")
                regime = _detect_market_regime_heuristic(bot, btc_data)
                bot.market_regime = regime
                _compute_hurst_on_btc(bot, btc_data)
                return regime

        regime, confidence = hmm_filter.predict_regime(btc_data)
        min_confidence = float(getattr(Config, "HMM_MIN_CONFIDENCE", 0.55))
        if regime == "UNKNOWN" or confidence < min_confidence:
            bot.log(f"⚠️ HMM regime fallback: regime={regime} confidence={confidence:.2f}")
            regime = _detect_market_regime_heuristic(bot, btc_data)
            bot.market_regime = regime
            _compute_hurst_on_btc(bot, btc_data)
            return regime

        bot.market_regime_confidence = confidence
        bot.market_regime_source = "HMM"
        bot.market_regime = regime
        if hasattr(hmm_filter, "predict_markov_snapshot"):
            snapshot = hmm_filter.predict_markov_snapshot(btc_data)
            if isinstance(snapshot, dict) and snapshot.get("is_ready"):
                _publish_hmm_snapshot(bot, snapshot)

        _compute_hurst_on_btc(bot, btc_data)

        return regime
    except Exception as error:
        bot.log(f"⚠️ Error detecting HMM market regime: {error}")
        regime = _detect_market_regime_heuristic(bot)
        bot.market_regime = regime
        _compute_hurst_on_btc(bot, btc_data)
        return regime

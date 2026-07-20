import threading
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import core.bot_market_state as market_state
from core.signals import analyze as signal_analyze
from core.signals import filters
from core.strategy.orchestrator import StrategyOrchestrator
from core.strategy.regime_hmm import DynamicHMMRegime


class DynamicHMMRegimeTests(unittest.TestCase):
    def test_maps_range_by_near_zero_direction_not_middle_label(self):
        feature_frame = pd.DataFrame(
            {
                "log_return": [-0.03, -0.02, 0.001, -0.001, 0.02, 0.03],
                "volatility": [0.01, 0.01, 0.04, 0.05, 0.01, 0.01],
                "dir_smooth": [-0.02, -0.02, 0.0, 0.0, 0.02, 0.02],
            }
        )
        hidden_states = np.array([2, 2, 0, 0, 1, 1])

        state_map = DynamicHMMRegime()._map_hidden_states(feature_frame, hidden_states)

        self.assertEqual(state_map[0], "RANGE")
        self.assertEqual(state_map[1], "BULL_TREND")
        self.assertEqual(state_map[2], "BEAR_TREND")

    def test_missing_hmmlearn_dependency_fails_closed(self):
        regime = DynamicHMMRegime()
        if regime.dependency_available():
            self.skipTest("hmmlearn is installed in this environment")

        self.assertFalse(regime.dynamic_retrain(pd.DataFrame({"close": [1.0] * 120})))
        self.assertEqual(
            regime.predict_regime(pd.DataFrame({"close": [1.0] * 120})), ("UNKNOWN", 0.0)
        )

    def test_markov_snapshot_exposes_transition_probabilities(self):
        class FakeModel:
            transmat_ = np.array(
                [
                    [0.10, 0.80, 0.10],
                    [0.15, 0.20, 0.65],
                    [0.70, 0.20, 0.10],
                ]
            )

            def predict_proba(self, _):
                return np.array([[0.10, 0.80, 0.10]])

        regime = DynamicHMMRegime()
        regime.model = FakeModel()
        regime.is_ready = True
        regime.state_map = {0: "BEAR_TREND", 1: "RANGE", 2: "BULL_TREND"}
        regime._transform_features = lambda _: np.array([[0.0, 0.0, 0.0]])

        snapshot = regime.predict_markov_snapshot(pd.DataFrame({"close": [1.0] * 30}))

        self.assertTrue(snapshot["is_ready"])
        self.assertEqual(snapshot["state"], "RANGE")
        self.assertAlmostEqual(snapshot["confidence"], 0.80)
        self.assertAlmostEqual(snapshot["bullish_breakout_prob"], 54.0)
        self.assertAlmostEqual(snapshot["bearish_reversal_prob"], 20.0)
        self.assertAlmostEqual(snapshot["range_prob"], 26.0)


class MarketStateHMMFallbackTests(unittest.TestCase):
    def test_cached_btc_requires_dict_data_and_minimum_history(self):
        short_data = pd.DataFrame({"close": [1.0] * 10})

        self.assertIsNone(market_state._get_cached_btc_1h(SimpleNamespace(data_service=None)))
        self.assertIsNone(
            market_state._get_cached_btc_1h(
                SimpleNamespace(data_service=SimpleNamespace(data_cache=[]))
            )
        )
        self.assertIsNone(
            market_state._get_cached_btc_1h(
                SimpleNamespace(
                    data_service=SimpleNamespace(data_cache={"BTC/USDT_1h": short_data})
                )
            )
        )

    def test_warmup_hmm_is_skipped_when_disabled(self):
        bot = SimpleNamespace(log=MagicMock())

        with patch.object(market_state.Config, "HMM_REGIME_ENABLED", False):
            self.assertFalse(market_state.warmup_hmm_regime(bot))

        bot.log.assert_not_called()

    def test_warmup_hmm_is_skipped_without_data_service(self):
        bot = SimpleNamespace(data_service=None, log=MagicMock())

        with patch.object(market_state.Config, "HMM_REGIME_ENABLED", True):
            self.assertFalse(market_state.warmup_hmm_regime(bot))

        bot.log.assert_called_once()

    def test_warmup_hmm_falls_back_when_exchange_returns_no_candles(self):
        data_service = SimpleNamespace(
            exchange=SimpleNamespace(fetch_ohlcv=MagicMock(return_value=[])),
            _track_api_weight=MagicMock(),
        )
        bot = SimpleNamespace(data_service=data_service, log=MagicMock())

        with patch.object(market_state.Config, "HMM_REGIME_ENABLED", True):
            with patch.object(market_state.Config, "HMM_BOOTSTRAP_CANDLES", 500):
                self.assertFalse(market_state.warmup_hmm_regime(bot))

        data_service.exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "1h", limit=500)
        data_service._track_api_weight.assert_called_once_with("fetch_ohlcv", 1, "market")

    def test_warmup_hmm_falls_back_when_training_rejects_data(self):
        close = pd.Series([100.0 + i for i in range(420)])
        ohlcv = [
            [i, float(price), float(price + 1), float(price - 1), float(price), 1000.0]
            for i, price in enumerate(close)
        ]
        data_service = SimpleNamespace(
            exchange=SimpleNamespace(fetch_ohlcv=MagicMock(return_value=ohlcv)),
            data_cache={},
            last_ohlcv_fetch={},
        )
        bot = SimpleNamespace(data_service=data_service, log=MagicMock())
        fake_hmm = SimpleNamespace(
            is_ready=False,
            dynamic_retrain=MagicMock(return_value=False),
            last_error="not enough variance",
        )

        with patch.object(market_state, "hmm_filter", fake_hmm):
            with patch.object(market_state.Config, "HMM_LOOKBACK_CANDLES", 336):
                self.assertFalse(market_state.warmup_hmm_regime(bot))

        fake_hmm.dynamic_retrain.assert_called_once()
        self.assertIn("BTC/USDT_1h", data_service.data_cache)

    def test_low_confidence_hmm_falls_back_to_heuristic(self):
        close = pd.Series([100.0 + i for i in range(220)])
        btc_data = pd.DataFrame(
            {
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "adx": [30.0] * 220,
            }
        )
        bot = SimpleNamespace(
            market_btc_price=btc_data["close"].iloc[-1],
            data_service=SimpleNamespace(fetch_and_update_data=lambda *_: btc_data),
            log=lambda *_: None,
        )
        fake_hmm = SimpleNamespace(
            is_ready=True,
            dynamic_retrain=lambda *_: True,
            predict_regime=lambda *_: ("RANGE", 0.20),
            last_error=None,
        )

        with patch.object(market_state, "hmm_filter", fake_hmm):
            with patch.object(market_state.Config, "HMM_MIN_CONFIDENCE", 0.55):
                self.assertEqual(market_state.detect_market_regime(bot), "BULL_TREND")

    def test_warmup_hmm_trains_synchronously_and_caches_btc_data(self):
        close = pd.Series([100.0 + i for i in range(420)])
        ohlcv = [
            [
                i,
                float(close.iloc[i]),
                float(close.iloc[i] + 1),
                float(close.iloc[i] - 1),
                float(close.iloc[i]),
                1000.0,
            ]
            for i in range(len(close))
        ]
        data_service = SimpleNamespace(
            exchange=SimpleNamespace(fetch_ohlcv=MagicMock(return_value=ohlcv)),
            data_cache={},
            last_ohlcv_fetch={},
            _clean_df=lambda df: df.drop_duplicates(subset=["time"]).sort_values("time"),
            _track_api_weight=MagicMock(),
        )
        bot = SimpleNamespace(data_service=data_service, log=MagicMock())
        fake_hmm = SimpleNamespace(
            is_ready=False,
            dynamic_retrain=MagicMock(return_value=True),
            last_error=None,
        )

        with patch.object(market_state, "hmm_filter", fake_hmm):
            with patch.object(market_state.Config, "HMM_BOOTSTRAP_CANDLES", 1000):
                with patch.object(market_state.Config, "HMM_LOOKBACK_CANDLES", 336):
                    self.assertTrue(market_state.warmup_hmm_regime(bot))

        data_service.exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "1h", limit=1000)
        fake_hmm.dynamic_retrain.assert_called_once()
        self.assertIn("BTC/USDT_1h", data_service.data_cache)

    def test_detect_market_regime_uses_warmup_cache_before_fetching(self):
        close = pd.Series([100.0 + i for i in range(220)])
        btc_data = pd.DataFrame(
            {
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "adx": [30.0] * 220,
            }
        )
        fetch_mock = MagicMock(return_value=btc_data)
        bot = SimpleNamespace(
            market_btc_price=btc_data["close"].iloc[-1],
            data_service=SimpleNamespace(
                data_cache={"BTC/USDT_1h": btc_data},
                fetch_and_update_data=fetch_mock,
            ),
            log=lambda *_: None,
        )
        fake_hmm = SimpleNamespace(
            is_ready=True,
            dynamic_retrain=lambda *_: True,
            predict_regime=lambda *_: ("BULL_TREND", 0.90),
            predict_markov_snapshot=lambda *_: {
                "ts": datetime.now(UTC).isoformat(),
                "is_ready": True,
                "state": "BULL_TREND",
                "bullish_breakout_prob": 80.0,
            },
            last_error=None,
        )

        with patch.object(market_state, "hmm_filter", fake_hmm):
            self.assertEqual(market_state.detect_market_regime(bot), "BULL_TREND")

        fetch_mock.assert_not_called()
        self.assertEqual(bot.hmm_markov_snapshot["state"], "BULL_TREND")

    def test_detect_market_regime_publishes_snapshot_and_persists_async(self):
        close = pd.Series([100.0 + i for i in range(220)])
        btc_data = pd.DataFrame(
            {
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "adx": [30.0] * 220,
            }
        )
        snapshot = {
            "ts": datetime.now(UTC).isoformat(),
            "is_ready": True,
            "state": "RANGE",
            "bullish_breakout_prob": 82.0,
        }
        brain = SimpleNamespace(set_metadata_json=MagicMock())
        bot = SimpleNamespace(
            market_btc_price=btc_data["close"].iloc[-1],
            data_service=SimpleNamespace(data_cache={"BTC/USDT_1h": btc_data}),
            brain=brain,
            db_lock=threading.Lock(),
            log=MagicMock(),
        )
        fake_hmm = SimpleNamespace(
            is_ready=True,
            dynamic_retrain=lambda *_: True,
            predict_regime=lambda *_: ("RANGE", 0.90),
            predict_markov_snapshot=lambda *_: snapshot,
            last_error=None,
        )

        with patch.object(market_state.Config, "MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS", 0):
            market_state._last_hmm_snapshot_persist_ts = None
            market_state._last_hmm_snapshot_persist_monotonic = 0.0
            with patch.object(market_state, "hmm_filter", fake_hmm):
                with patch.object(market_state.threading, "Thread") as thread_cls:
                    thread_obj = MagicMock()
                    thread_obj.start.side_effect = lambda: thread_cls.call_args.kwargs["target"]()
                    thread_cls.return_value = thread_obj
                    self.assertEqual(market_state.detect_market_regime(bot), "RANGE")

        self.assertEqual(bot.hmm_markov_snapshot, snapshot)
        brain.set_metadata_json.assert_called_once_with("hmm_markov_snapshot", snapshot)

    def test_detect_market_regime_uses_heuristic_when_hmm_disabled(self):
        close = pd.Series([300.0 - i for i in range(220)])
        btc_data = pd.DataFrame(
            {
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "adx": [35.0] * 220,
            }
        )
        bot = SimpleNamespace(
            market_btc_price=btc_data["close"].iloc[-1],
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock(return_value=btc_data)),
            log=MagicMock(),
        )

        with patch.object(market_state.Config, "HMM_REGIME_ENABLED", False):
            self.assertEqual(market_state.detect_market_regime(bot), "BEAR_TREND")

        self.assertEqual(bot.market_regime, "BEAR_TREND")
        self.assertEqual(bot.market_regime_source, "HEURISTIC")

    def test_detect_market_regime_falls_back_to_range_without_btc_price(self):
        bot = SimpleNamespace(
            market_btc_price=0,
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock()),
            log=MagicMock(),
        )

        with patch.object(market_state.Config, "HMM_REGIME_ENABLED", False):
            self.assertEqual(market_state.detect_market_regime(bot), "RANGE")

        bot.data_service.fetch_and_update_data.assert_not_called()
        self.assertEqual(bot.market_regime_source, "HEURISTIC")

    def test_detect_market_regime_handles_hmm_predict_exception(self):
        close = pd.Series([100.0 + i for i in range(220)])
        btc_data = pd.DataFrame(
            {
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "adx": [30.0] * 220,
            }
        )
        bot = SimpleNamespace(
            market_btc_price=btc_data["close"].iloc[-1],
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock(return_value=btc_data)),
            log=MagicMock(),
        )
        fake_hmm = SimpleNamespace(
            is_ready=True,
            dynamic_retrain=MagicMock(return_value=True),
            predict_regime=MagicMock(side_effect=RuntimeError("boom")),
            last_error=None,
        )

        with patch.object(market_state, "hmm_filter", fake_hmm):
            self.assertEqual(market_state.detect_market_regime(bot), "BULL_TREND")

        bot.log.assert_called()
        self.assertEqual(bot.market_regime, "BULL_TREND")


class RegimeRangeFilterTests(unittest.TestCase):
    def _build_bot(self, btc_regime="RANGE"):
        breakout_agent = MagicMock()
        breakout_agent.evaluate_breakout.return_value = (False, None)
        breakout_agent.add_to_watchlist.return_value = False
        return SimpleNamespace(
            db_lock=threading.Lock(),
            brain=SimpleNamespace(
                get_genetic_params=lambda *_: {},
                get_stats_by_trend=lambda: {},
            ),
            breakout_agent=breakout_agent,
            _get_market_regime=lambda: btc_regime,
            _get_shock_distance_pct=lambda *_: (None, None),
            _calculate_quant_consensus=lambda prob, ctx: (prob, "N/A"),
            log=lambda *_: None,
            bootstrap_heuristic_mode=False,
        )

    def test_range_penalty_halves_entry_probability(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }
        bot = self._build_bot("RANGE")

        with patch.object(filters.Config, "HMM_RANGE_PENALTY", 0.5):
            with patch.object(filters.Config, "HMM_RANGE_VETO", False):
                with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                    with patch.object(
                        filters.Strategy,
                        "check_entry_filters",
                        return_value=(True, "OK", "CALM", {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0}),
                    ):
                        prob_final, filter_passed, filter_reason, updated_ctx = (
                            filters._apply_entry_filters_and_adjust_prob(
                                bot,
                                "TEST/USDT",
                                "TEST/USDT",
                                pd.DataFrame(),
                                "BUY",
                                80.0,
                                ctx,
                                1.0,
                            )
                        )

        self.assertEqual(prob_final, 40.0)
        self.assertTrue(filter_passed)
        self.assertEqual(filter_reason, "OK")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_PENALTY")

    def test_range_veto_blocks_entry_without_markov_snapshot_and_skips_breakout_scan(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }
        bot = self._build_bot("RANGE")

        with patch.object(filters.Config, "HMM_RANGE_PENALTY", 0.5):
            with patch.object(filters.Config, "HMM_RANGE_VETO", True):
                with patch.object(filters.Config, "PAPER_MODE", False):
                    with patch.object(filters, "append_execution_event") as event_mock:
                        with patch.object(
                            filters.Strategy,
                            "check_entry_filters",
                            return_value=(
                                True,
                                "OK",
                                "CALM",
                                {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                            ),
                        ):
                            prob_final, filter_passed, filter_reason, updated_ctx = (
                                filters._apply_entry_filters_and_adjust_prob(
                                    bot,
                                    "TEST/USDT",
                                    "TEST/USDT",
                                    pd.DataFrame(),
                                    "BUY",
                                    80.0,
                                    ctx,
                                    1.0,
                                )
                            )

        self.assertEqual(prob_final, 0.0)
        self.assertFalse(filter_passed)
        self.assertEqual(filter_reason, "RANGE REGIME VETO")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_VETO")
        emitted_events = [call.args[1] for call in event_mock.call_args_list]
        self.assertIn("RANGE_VETO", emitted_events)
        self.assertIn("FILTER_APPLIED", emitted_events)
        bot.breakout_agent.evaluate_breakout.assert_not_called()

    def test_range_veto_blocks_shadow_learning_by_default(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }
        bot = self._build_bot("RANGE")
        bot.execution_mode = "shadow_live"

        with patch.object(filters.Config, "HMM_RANGE_PENALTY", 0.5):
            with patch.object(filters.Config, "HMM_RANGE_VETO", True):
                with patch.object(filters.Config, "PAPER_MODE", True):
                    with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                        with (
                            patch.object(filters.Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
                            patch.object(
                                filters.Strategy,
                                "check_entry_filters",
                                return_value=(
                                    True,
                                    "OK",
                                    "CALM",
                                    {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                                ),
                            ),
                        ):
                            prob_final, filter_passed, filter_reason, updated_ctx = (
                                filters._apply_entry_filters_and_adjust_prob(
                                    bot,
                                    "TEST/USDT",
                                    "TEST/USDT",
                                    pd.DataFrame(),
                                    "BUY",
                                    80.0,
                                    ctx,
                                    1.0,
                                )
                            )

        self.assertEqual(prob_final, 0.0)
        self.assertFalse(filter_passed)
        self.assertEqual(filter_reason, "RANGE REGIME VETO")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_VETO")

    def test_range_veto_allows_shadow_learning_only_with_override(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }
        bot = self._build_bot("RANGE")
        bot.execution_mode = "shadow_live"

        with patch.object(filters.Config, "HMM_RANGE_PENALTY", 0.5):
            with patch.object(filters.Config, "HMM_RANGE_VETO", True):
                with patch.object(filters.Config, "PAPER_MODE", True):
                    with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                        with (
                            patch.object(
                                filters.Config, "HMM_RANGE_LEARNING_OVERRIDE_ENABLED", True
                            ),
                            patch.object(filters.Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
                            patch.object(
                                filters.Strategy,
                                "check_entry_filters",
                                return_value=(
                                    True,
                                    "OK",
                                    "CALM",
                                    {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                                ),
                            ),
                        ):
                            prob_final, filter_passed, filter_reason, updated_ctx = (
                                filters._apply_entry_filters_and_adjust_prob(
                                    bot,
                                    "TEST/USDT",
                                    "TEST/USDT",
                                    pd.DataFrame(),
                                    "BUY",
                                    80.0,
                                    ctx,
                                    1.0,
                                )
                            )

        self.assertEqual(prob_final, 40.0)
        self.assertTrue(filter_passed)
        self.assertEqual(filter_reason, "OK")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_PENALTY")

    def test_range_veto_does_not_allow_shadow_prospect_in_real_mode_without_markov(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }
        bot = self._build_bot("RANGE")
        bot.execution_mode = "shadow_live"

        with patch.object(filters.Config, "HMM_RANGE_PENALTY", 0.5):
            with patch.object(filters.Config, "HMM_RANGE_VETO", True):
                with patch.object(filters.Config, "PAPER_MODE", False):
                    with patch.object(filters.Config, "EXECUTION_BACKEND", "shadow_live"):
                        with patch.object(
                            filters.Strategy,
                            "check_entry_filters",
                            return_value=(
                                True,
                                "OK",
                                "CALM",
                                {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                            ),
                        ):
                            prob_final, filter_passed, filter_reason, updated_ctx = (
                                filters._apply_entry_filters_and_adjust_prob(
                                    bot,
                                    "TEST/USDT",
                                    "TEST/USDT",
                                    pd.DataFrame(),
                                    "BUY",
                                    50.0,
                                    ctx,
                                    1.0,
                                )
                            )

        self.assertEqual(prob_final, 0.0)
        self.assertFalse(filter_passed)
        self.assertEqual(filter_reason, "RANGE REGIME VETO")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_VETO")

    def test_markov_range_breakout_relaxes_range_veto_and_penalizes_lightly(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
            "hmm_data": {
                "ts": datetime.now(UTC).isoformat(),
                "is_ready": True,
                "state": "RANGE",
                "bullish_breakout_prob": 82.0,
                "bearish_reversal_prob": 10.0,
            },
        }
        bot = self._build_bot("RANGE")

        with patch.object(filters.Config, "HMM_RANGE_VETO", True):
            with patch.object(filters.Config, "PAPER_MODE", False):
                with patch.object(filters.Config, "MARKOV_BREAKOUT_MIN", 75.0):
                    with patch.object(filters.Config, "MARKOV_RANGE_BREAKOUT_WEIGHT", 0.90):
                        with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                            with (
                                patch.object(
                                    filters.Config, "HMM_RANGE_LEARNING_OVERRIDE_ENABLED", True
                                ),
                                patch.object(
                                    filters.Strategy,
                                    "check_entry_filters",
                                    return_value=(
                                        True,
                                        "OK",
                                        "CALM",
                                        {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                                    ),
                                ),
                            ):
                                prob_final, filter_passed, filter_reason, updated_ctx = (
                                    filters._apply_entry_filters_and_adjust_prob(
                                        bot,
                                        "TEST/USDT",
                                        "TEST/USDT",
                                        pd.DataFrame(),
                                        "BUY",
                                        80.0,
                                        ctx,
                                        1.0,
                                    )
                                )

        self.assertEqual(prob_final, 72.0)
        self.assertTrue(filter_passed)
        self.assertEqual(filter_reason, "RANGE_BREAKOUT_ANTICIPATION")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_BREAKOUT_ANTICIPATION")
        self.assertEqual(updated_ctx["markov_prob"], 82.0)
        self.assertEqual(bot.markov_decision_stats["range_breakout_allowed"], 1)

    def test_markov_range_breakout_cannot_override_hard_range_veto_by_default(self):
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
            "hmm_data": {
                "ts": datetime.now(UTC).isoformat(),
                "is_ready": True,
                "state": "RANGE",
                "bullish_breakout_prob": 90.0,
            },
        }
        bot = self._build_bot("RANGE")

        with patch.object(filters.Config, "HMM_RANGE_VETO", True):
            with patch.object(filters.Config, "MARKOV_BREAKOUT_MIN", 75.0):
                with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                    with patch.object(
                        filters.Strategy,
                        "check_entry_filters",
                        return_value=(
                            True,
                            "OK",
                            "CALM",
                            {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                        ),
                    ):
                        prob_final, filter_passed, filter_reason, updated_ctx = (
                            filters._apply_entry_filters_and_adjust_prob(
                                bot,
                                "TEST/USDT",
                                "TEST/USDT",
                                pd.DataFrame(),
                                "BUY",
                                80.0,
                                ctx,
                                1.0,
                            )
                        )

        self.assertEqual(prob_final, 0.0)
        self.assertFalse(filter_passed)
        self.assertEqual(filter_reason, "RANGE REGIME VETO")
        self.assertEqual(updated_ctx["regime_reason"], "RANGE_VETO")
        self.assertEqual(bot.markov_decision_stats["range_hard_veto"], 1)

    def test_markov_range_stagnant_applies_penalty(self):
        """[HOTFIX v118.1] Dead zone ahora aplica penalización estándar (x0.75)
        en lugar de veto total. El mercado lateral estancado reduce probabilidades
        pero no bloquea señales válidas."""
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
            "hmm_data": {
                "ts": datetime.now(UTC).isoformat(),
                "is_ready": True,
                "state": "RANGE",
                "bullish_breakout_prob": 20.0,
            },
        }
        bot = self._build_bot("RANGE")

        with patch.object(filters.Config, "HMM_RANGE_VETO", True):
            with patch.object(filters.Config, "PAPER_MODE", False):
                with patch.object(filters.Config, "MARKOV_DEAD_ZONE_MAX", 30.0):
                    with patch.object(filters.Config, "MARKOV_RANGE_STANDARD_WEIGHT", 0.75):
                        with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                            with (
                                patch.object(
                                    filters.Config, "HMM_RANGE_LEARNING_OVERRIDE_ENABLED", True
                                ),
                                patch.object(
                                    filters.Strategy,
                                    "check_entry_filters",
                                    return_value=(
                                        True,
                                        "OK",
                                        "CALM",
                                        {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                                    ),
                                ),
                            ):
                                prob_final, filter_passed, filter_reason, updated_ctx = (
                                    filters._apply_entry_filters_and_adjust_prob(
                                        bot,
                                        "TEST/USDT",
                                        "TEST/USDT",
                                        pd.DataFrame(),
                                        "BUY",
                                        80.0,
                                        ctx,
                                        1.0,
                                    )
                                )

        # Penalización: 80 * 0.75 = 60.0 — no veto total (HOTFIX v118.1)
        self.assertEqual(prob_final, 60.0)
        self.assertTrue(filter_passed)
        self.assertEqual(updated_ctx["regime_reason"], "HMM_RANGE_PENALTY")
        self.assertEqual(bot.markov_decision_stats["range_dead_zone_penalty"], 1)

    def test_stale_markov_snapshot_can_penalize_but_not_boost(self):
        stale_ts = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        ctx = {
            "rsi": 55.0,
            "adx": 22.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "UP",
            "tier": "IRON",
            "hmm_data": {
                "ts": stale_ts,
                "is_ready": True,
                "state": "BULL_TREND",
                "bullish_breakout_prob": 90.0,
            },
        }
        bot = self._build_bot("BULL_TREND")

        with patch.object(filters.Config, "MARKOV_SNAPSHOT_MAX_AGE_SECONDS", 3600.0):
            with patch.object(filters.Config, "MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 3600.0):
                with patch.object(filters.Config, "MARKOV_BULL_STRONG_WEIGHT", 1.10):
                    with patch.object(filters.Config, "SIDE_PARITY_FILTER_ENABLED", False):
                        with (
                            patch.object(filters.Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
                            patch.object(
                                filters.Strategy,
                                "check_entry_filters",
                                return_value=(
                                    True,
                                    "OK",
                                    "CALM",
                                    {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0},
                                ),
                            ),
                        ):
                            prob_final, filter_passed, filter_reason, updated_ctx = (
                                filters._apply_entry_filters_and_adjust_prob(
                                    bot,
                                    "TEST/USDT",
                                    "TEST/USDT",
                                    pd.DataFrame(),
                                    "BUY",
                                    80.0,
                                    ctx,
                                    1.0,
                                )
                            )

        self.assertEqual(prob_final, 80.0)
        self.assertTrue(filter_passed)
        self.assertEqual(updated_ctx["markov_snapshot_mode"], "stale_penalty_only")

    def test_directional_regimes_keep_or_boost_aligned_weight(self):
        bull_weight, bull_reason, bull_veto = filters._resolve_btc_regime_adjustment(
            "BUY", "BULL_TREND"
        )
        bear_weight, bear_reason, bear_veto = filters._resolve_btc_regime_adjustment(
            "SELL", "BEAR_TREND"
        )

        self.assertGreaterEqual(bull_weight, 1.0)
        self.assertEqual(bull_reason, "BULL_ALIGNED")
        self.assertFalse(bull_veto)
        self.assertGreaterEqual(bear_weight, 1.0)
        self.assertEqual(bear_reason, "BEAR_ALIGNED")
        self.assertFalse(bear_veto)


class RegimePreVetoTests(unittest.TestCase):
    def _build_df(self):
        close = pd.Series([100.0 + (i * 0.01) for i in range(60)])
        return pd.DataFrame(
            {
                "close": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "ema": close,
            }
        )

    def _build_bot(self, regime="RANGE"):
        return SimpleNamespace(
            force_chaos_mode=False,
            current_sentiment=("NEUTRAL",),
            db_lock=threading.Lock(),
            brain=SimpleNamespace(get_dynamic_settings=lambda *_: {}),
            global_rag_impact=0.0,
            ghost_model=object(),
            scaler=None,
            market_btc_change_tf=0.0,
            _get_market_regime=lambda: regime,
            update_radar=MagicMock(),
            log=MagicMock(),
        )

    def test_pre_veto_range_blocks_real_capital_without_markov_snapshot(self):
        bot = self._build_bot("RANGE")
        df = self._build_df()

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(signal_analyze.Config, "PAPER_MODE", False):
                with patch.object(signal_analyze.Strategy, "analyze") as analyze_mock:
                    result = signal_analyze._analyze_symbol_candidate(
                        bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                    )

        self.assertIsNone(result)
        analyze_mock.assert_not_called()
        bot.update_radar.assert_called_once()

    def test_pre_veto_range_allows_shadow_learning(self):
        bot = self._build_bot("RANGE")
        bot.execution_mode = "shadow_live"
        df = self._build_df()
        expected = ("BUY", "NONE", 100.0, 10.0, {}, {})

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(signal_analyze.Config, "PAPER_MODE", True):
                with patch.object(
                    signal_analyze.Strategy,
                    "analyze",
                    return_value=expected,
                ) as analyze_mock:
                    result = signal_analyze._analyze_symbol_candidate(
                        bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                    )

        self.assertEqual(result, expected)
        analyze_mock.assert_called_once()
        self.assertEqual(analyze_mock.call_args.kwargs["market_regime"], "RANGE")

    def test_pre_veto_range_blocks_real_even_if_shadow_backend_is_set_without_markov(self):
        bot = self._build_bot("RANGE")
        bot.execution_mode = "shadow_live"
        df = self._build_df()

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(signal_analyze.Config, "PAPER_MODE", False):
                with patch.object(signal_analyze.Config, "EXECUTION_BACKEND", "shadow_live"):
                    with patch.object(signal_analyze.Strategy, "analyze") as analyze_mock:
                        result = signal_analyze._analyze_symbol_candidate(
                            bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                        )

        self.assertIsNone(result)
        analyze_mock.assert_not_called()

    def test_pre_veto_range_allows_real_analysis_when_markov_not_bearish_extreme(self):
        bot = self._build_bot("RANGE")
        bot.hmm_markov_snapshot = {
            "ts": datetime.now(UTC).isoformat(),
            "is_ready": True,
            "state": "RANGE",
            "bearish_reversal_prob": 35.0,
        }
        df = self._build_df()
        expected = ("BUY", "NONE", 100.0, 10.0, {}, {})

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(signal_analyze.Config, "PAPER_MODE", False):
                with patch.object(
                    signal_analyze.Strategy,
                    "analyze",
                    return_value=expected,
                ) as analyze_mock:
                    result = signal_analyze._analyze_symbol_candidate(
                        bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                    )

        self.assertEqual(result, expected)
        analyze_mock.assert_called_once()

    def test_pre_veto_range_allows_directional_strategy_when_markov_bearish_extreme(self):
        bot = self._build_bot("RANGE")
        bot.hmm_markov_snapshot = {
            "ts": datetime.now(UTC).isoformat(),
            "is_ready": True,
            "state": "RANGE",
            "bearish_reversal_prob": 90.0,
        }
        df = self._build_df()
        expected = ("SELL", "NONE", 100.0, 10.0, {}, {})

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(signal_analyze.Config, "PAPER_MODE", False):
                with patch.object(
                    signal_analyze.Config, "MARKOV_PREVETO_BEARISH_REVERSAL_MIN", 85.0
                ):
                    with patch.object(
                        signal_analyze.Strategy, "analyze", return_value=expected
                    ) as analyze_mock:
                        result = signal_analyze._analyze_symbol_candidate(
                            bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                        )

        self.assertEqual(result, expected)
        analyze_mock.assert_called_once()

    def test_pre_veto_disabled_allows_strategy_analyze(self):
        bot = self._build_bot("RANGE")
        df = self._build_df()
        expected = ("BUY", "NONE", 100.0, 10.0, {}, {})

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", False):
            with patch.object(
                signal_analyze.Strategy,
                "analyze",
                return_value=expected,
            ) as analyze_mock:
                result = signal_analyze._analyze_symbol_candidate(
                    bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                )

        self.assertEqual(result, expected)
        analyze_mock.assert_called_once()
        self.assertEqual(analyze_mock.call_args.kwargs["market_regime"], "RANGE")

    def test_pre_veto_ignores_directional_regime(self):
        bot = self._build_bot("BULL_TREND")
        df = self._build_df()
        expected = ("BUY", "NONE", 100.0, 10.0, {}, {})

        with patch.object(signal_analyze.Config, "HMM_RANGE_VETO", True):
            with patch.object(
                signal_analyze.Strategy,
                "analyze",
                return_value=expected,
            ) as analyze_mock:
                result = signal_analyze._analyze_symbol_candidate(
                    bot, "TEST/USDT", "TEST/USDT", df, df, elapsed=12
                )

        self.assertEqual(result, expected)
        analyze_mock.assert_called_once()
        self.assertEqual(analyze_mock.call_args.kwargs["market_regime"], "BULL_TREND")


class HMMOrchestratorWeightsTests(unittest.TestCase):
    def test_orchestrator_uses_hmm_directional_regimes_directly(self):
        orchestrator = StrategyOrchestrator()

        bull_weights = orchestrator.get_adaptive_weights("BULL_TREND", adx=30.0, rsi=55.0)
        bear_weights = orchestrator.get_adaptive_weights("BEAR_TREND", adx=30.0, rsi=45.0)

        self.assertGreater(bull_weights["MT"], bull_weights["SR"])
        self.assertGreater(bear_weights["MT"], bear_weights["SR"])
        self.assertAlmostEqual(sum(bull_weights.values()), 1.0)
        self.assertAlmostEqual(sum(bear_weights.values()), 1.0)

    def test_orchestrator_deweights_trend_agent_in_range(self):
        orchestrator = StrategyOrchestrator()

        range_weights = orchestrator.get_adaptive_weights("RANGE", adx=12.0, rsi=70.0)

        self.assertLess(range_weights["MT"], range_weights["SR"])
        self.assertLess(range_weights["MT"], range_weights["G"])
        self.assertGreaterEqual(range_weights["SR"], 0.45)
        self.assertAlmostEqual(sum(range_weights.values()), 1.0)

    def test_correlation_veto_does_not_apply_before_30_samples(self):
        orchestrator = StrategyOrchestrator()
        weights = {"MT": 0.4, "SR": 0.3, "G": 0.3}
        performances = {"MT": 70.0, "SR": 90.0, "G": 100.0}

        for idx in range(29):
            adjusted = orchestrator._apply_correlation_veto(
                weights,
                {"MT": float(idx), "SR": float(idx), "G": float((idx * idx) % 17)},
                performances,
            )

        self.assertEqual(adjusted, weights)
        self.assertEqual(len(orchestrator.vote_history["MT"]), 29)

    def test_correlation_veto_applies_at_30_samples_and_keeps_full_window(self):
        orchestrator = StrategyOrchestrator()
        weights = {"MT": 0.4, "SR": 0.3, "G": 0.3}
        performances = {"MT": 70.0, "SR": 90.0, "G": 100.0}

        for idx in range(30):
            adjusted = orchestrator._apply_correlation_veto(
                weights,
                {"MT": float(idx), "SR": float(idx), "G": float((idx * idx) % 17)},
                performances,
            )

        self.assertEqual(adjusted["MT"], 0.0)
        self.assertEqual(adjusted["SR"], weights["SR"])
        self.assertEqual(len(orchestrator.vote_history["MT"]), 30)

        for idx in range(30, 35):
            orchestrator._apply_correlation_veto(
                weights,
                {"MT": float(idx), "SR": float(idx), "G": float((idx * idx) % 17)},
                performances,
            )

        self.assertEqual(len(orchestrator.vote_history["MT"]), 30)


if __name__ == "__main__":
    unittest.main()

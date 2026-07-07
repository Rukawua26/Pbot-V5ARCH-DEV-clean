import unittest

import numpy as np
import pandas as pd

from core.strategy.hurst import HurstEstimator
from core.strategy.orchestrator import StrategyOrchestrator


class HurstEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.estimator = HurstEstimator(window=128, max_lag=64, min_lag=10)

    def _make_ar_returns(self, coef: float, n: int = 2000) -> np.ndarray:
        np.random.seed(42)
        r = [np.random.normal(0, 0.02)]
        for _ in range(1, n):
            r.append(coef * r[-1] + (1 - abs(coef)) * np.random.normal(0, 0.02))
        return np.array(r)

    def test_random_walk_returns_near_05(self):
        returns = self._make_ar_returns(0.0)
        h = self.estimator.compute(returns)
        self.assertGreater(h, 0.30)
        self.assertLess(h, 0.70)

    def test_trending_returns_above_05(self):
        returns = self._make_ar_returns(0.3)
        h = self.estimator.compute(returns)
        self.assertGreater(h, 0.50)

    def test_mean_reverting_returns_below_05(self):
        returns = self._make_ar_returns(-0.3)
        h = self.estimator.compute(returns)
        self.assertLess(h, 0.60)

    def test_short_series_returns_05(self):
        prices = np.array([100.0, 101.0, 102.0])
        h = self.estimator.compute(prices)
        self.assertEqual(h, 0.5)

    def test_constant_series_returns_05(self):
        prices = np.ones(500) * 100.0
        h = self.estimator.compute(prices)
        self.assertEqual(h, 0.5)

    def test_very_short_series_returns_05(self):
        h = self.estimator.compute(np.array([100.0]))
        self.assertEqual(h, 0.5)

    def test_classify_persistent(self):
        self.assertEqual(HurstEstimator.classify(0.61), "PERSISTENT")
        self.assertEqual(HurstEstimator.classify(0.70), "PERSISTENT")
        self.assertEqual(HurstEstimator.classify(0.80), "PERSISTENT")

    def test_classify_antipersistent(self):
        self.assertEqual(HurstEstimator.classify(0.39), "ANTIPERSISTENT")
        self.assertEqual(HurstEstimator.classify(0.30), "ANTIPERSISTENT")
        self.assertEqual(HurstEstimator.classify(0.20), "ANTIPERSISTENT")

    def test_classify_random_walk(self):
        self.assertEqual(HurstEstimator.classify(0.50), "RANDOM_WALK")
        self.assertEqual(HurstEstimator.classify(0.48), "RANDOM_WALK")
        self.assertEqual(HurstEstimator.classify(0.52), "RANDOM_WALK")
        self.assertEqual(HurstEstimator.classify(0.55), "RANDOM_WALK")
        self.assertEqual(HurstEstimator.classify(0.45), "RANDOM_WALK")

    def test_classify_tolerant(self):
        self.assertEqual(HurstEstimator.classify_tolerant(0.56), "PERSISTENT")
        self.assertEqual(HurstEstimator.classify_tolerant(0.44), "ANTIPERSISTENT")
        self.assertEqual(HurstEstimator.classify_tolerant(0.50), "RANDOM_WALK")

    def test_confidence_at_extremes(self):
        self.assertGreater(HurstEstimator.confidence(0.9), 0.9)
        self.assertGreater(HurstEstimator.confidence(0.1), 0.9)
        self.assertAlmostEqual(HurstEstimator.confidence(0.5), 0.0, places=6)

    def test_to_snapshot(self):
        snap = HurstEstimator.to_snapshot(0.65)
        self.assertEqual(snap["hurst"], 0.65)
        self.assertEqual(snap["class"], "PERSISTENT")
        self.assertTrue(snap["ready"])

        snap_none = HurstEstimator.to_snapshot(None)
        self.assertIsNone(snap_none["hurst"])
        self.assertEqual(snap_none["class"], "UNKNOWN")
        self.assertFalse(snap_none["ready"])

    def test_compute_from_dataframe(self):
        returns = self._make_ar_returns(0.0, 500)
        price = 100 + np.cumsum(returns)
        df = pd.DataFrame({"close": price})
        h = self.estimator.compute_from_df(df)
        self.assertGreater(h, 0.30)
        self.assertLess(h, 0.70)

    def test_compute_from_empty_df_returns_05(self):
        h = self.estimator.compute_from_df(pd.DataFrame())
        self.assertEqual(h, 0.5)

    def test_compute_from_df_missing_col_returns_05(self):
        df = pd.DataFrame({"wrong_col": [1, 2, 3]})
        h = self.estimator.compute_from_df(df)
        self.assertEqual(h, 0.5)

    def test_rolling_returns_series(self):
        returns = self._make_ar_returns(0.0, 500)
        price = 100 + np.cumsum(returns)
        df = pd.DataFrame({"close": price})
        estimator = HurstEstimator(window=64, max_lag=32, min_lag=8)
        rolling = estimator.compute_rolling(df)
        self.assertIsInstance(rolling, pd.Series)
        self.assertEqual(len(rolling), len(df))
        self.assertTrue(pd.isna(rolling.iloc[estimator.window - 2]))
        self.assertFalse(pd.isna(rolling.iloc[-1]))

    def test_last_value_cached(self):
        returns = self._make_ar_returns(0.0, 500)
        h = self.estimator.compute(returns)
        self.assertEqual(self.estimator._last_value, h)

    def test_deterministic_on_same_data(self):
        returns = self._make_ar_returns(0.0, 500)
        h1 = self.estimator.compute(returns.copy())
        estimator2 = HurstEstimator(window=128, max_lag=64, min_lag=10)
        h2 = estimator2.compute(returns.copy())
        self.assertAlmostEqual(h1, h2, places=4)


class HurstOrchestratorIntegrationTests(unittest.TestCase):
    """Verify Hurst adjusts agent weights correctly."""

    def setUp(self):
        self.orchestrator = StrategyOrchestrator()

    def test_persistent_boosts_mt_in_bull(self):
        weights = self.orchestrator.get_adaptive_weights(regime="BULL_TREND", hurst=0.65)
        base_mt = self.orchestrator._base_weights["BULL_TREND"]["MT"]
        self.assertGreater(weights["MT"], base_mt)

    def test_antipersistent_boosts_sr_in_range(self):
        weights = self.orchestrator.get_adaptive_weights(regime="RANGE", hurst=0.35)
        base_sr = self.orchestrator._base_weights["RANGE"]["SR"]
        self.assertGreater(weights["SR"], base_sr)

    def test_random_hurst_does_not_change_weights(self):
        weights_base = self.orchestrator.get_adaptive_weights(regime="RANGE")
        weights_hurst = self.orchestrator.get_adaptive_weights(regime="RANGE", hurst=0.50)
        for agent in ["MT", "SR", "G"]:
            self.assertAlmostEqual(weights_base[agent], weights_hurst[agent], places=4)

    def test_none_hurst_does_not_change_weights(self):
        weights_base = self.orchestrator.get_adaptive_weights(regime="BULL_TREND")
        weights_none = self.orchestrator.get_adaptive_weights(regime="BULL_TREND", hurst=None)
        self.assertEqual(weights_base, weights_none)

    def test_weights_normalize_after_hurst_adjustment(self):
        weights = self.orchestrator.get_adaptive_weights(regime="BEAR_TREND", hurst=0.65)
        total = sum(weights.values())
        self.assertAlmostEqual(total, 1.0, places=4)

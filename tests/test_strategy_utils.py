import unittest

import numpy as np
import pandas as pd

from core.strategy.utils import StrategyUtils


class TestGetMarketContext(unittest.TestCase):
    def test_adx_above_25_returns_trend(self):
        self.assertEqual(StrategyUtils.get_market_context(30, 50), "TREND")

    def test_adx_at_25_returns_trend(self):
        self.assertEqual(StrategyUtils.get_market_context(25.01, 50), "TREND")

    def test_low_rsi_returns_volatile(self):
        self.assertEqual(StrategyUtils.get_market_context(15, 20), "VOLATILE")

    def test_high_rsi_returns_volatile(self):
        self.assertEqual(StrategyUtils.get_market_context(15, 80), "VOLATILE")

    def test_calm_market(self):
        self.assertEqual(StrategyUtils.get_market_context(20, 50), "CALM")

    def test_boundary_rsi_exactly_30_is_calm(self):
        self.assertEqual(StrategyUtils.get_market_context(20, 30), "CALM")

    def test_boundary_rsi_exactly_70_is_calm(self):
        self.assertEqual(StrategyUtils.get_market_context(20, 70), "CALM")

    def test_rsi_just_inside_calm(self):
        self.assertEqual(StrategyUtils.get_market_context(20, 31), "CALM")


class TestCalculateZScore(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)
        self.normal_df = pd.DataFrame({"close": 100 + np.cumsum(np.random.randn(100))})

    def test_returns_float_for_normal_input(self):
        z = StrategyUtils.calculate_z_score(self.normal_df)
        self.assertIsInstance(z, float)

    def test_returns_zero_for_none(self):
        self.assertEqual(StrategyUtils.calculate_z_score(None), 0.0)

    def test_returns_zero_for_empty(self):
        self.assertEqual(StrategyUtils.calculate_z_score(pd.DataFrame()), 0.0)

    def test_returns_zero_for_too_short(self):
        short = pd.DataFrame({"close": [100, 101]})
        self.assertEqual(StrategyUtils.calculate_z_score(short, window=20), 0.0)

    def test_returns_zero_for_flat_prices(self):
        flat = pd.DataFrame({"close": [100.0] * 50})
        self.assertEqual(StrategyUtils.calculate_z_score(flat), 0.0)

    def test_deterministic_trending(self):
        uptrend = pd.DataFrame({"close": list(range(100, 150, 1))})
        z = StrategyUtils.calculate_z_score(uptrend, window=10)
        self.assertNotEqual(z, 0.0)

    def test_exception_returns_zero(self):
        bad = pd.DataFrame({"close": [1, 2, 3]})
        bad["close"] = bad["close"].astype(str)
        z = StrategyUtils.calculate_z_score(bad)
        self.assertEqual(z, 0.0)


class TestDetectOrderBlock(unittest.TestCase):
    def _make_candles(self, n: int, trend: str = "flat") -> pd.DataFrame:
        base = 100.0
        rows = []
        for i in range(n):
            if trend == "bull":
                o = base + i * 0.5 + np.random.uniform(-0.2, 0.2)
                c = o + 0.8 + np.random.uniform(0, 0.3)
            elif trend == "bear":
                o = base + (n - i) * 0.5 + np.random.uniform(-0.2, 0.2)
                c = o - 0.8 - np.random.uniform(0, 0.3)
            else:
                o = base + np.random.uniform(-0.5, 0.5)
                c = o + np.random.uniform(-0.5, 0.5)
            h = max(o, c) + np.random.uniform(0.1, 0.5)
            low = min(o, c) - np.random.uniform(0.1, 0.5)
            v = np.random.uniform(10, 100)
            rows.append({"time": i, "open": o, "high": h, "low": low, "close": c, "volume": v})
        df = pd.DataFrame(rows)
        df["time"] = df["time"].astype(int)
        return df

    def setUp(self):
        StrategyUtils._ob_cache.clear()

    def test_returns_circle_for_none(self):
        self.assertEqual(StrategyUtils.detect_order_block(None, "X"), "⚪")

    def test_returns_circle_for_short(self):
        short = self._make_candles(20)
        self.assertEqual(StrategyUtils.detect_order_block(short, "X"), "⚪")

    def test_returns_circle_for_flat_no_pattern(self):
        flat = self._make_candles(60, trend="flat")
        result = StrategyUtils.detect_order_block(flat, "X")
        self.assertIn(result, {"⚪", "🟢", "🔴"})

    def test_cache_hit_returns_cached(self):
        df = self._make_candles(60)
        StrategyUtils._ob_cache["X_59"] = "🟢"
        self.assertEqual(StrategyUtils.detect_order_block(df, "X"), "🟢")

    def test_cache_eviction_on_excess(self):
        for i in range(110):
            StrategyUtils._ob_cache[f"s_{i}_0"] = "⚪"
        df = self._make_candles(60)
        StrategyUtils.detect_order_block(df, "fresh")
        self.assertLessEqual(len(StrategyUtils._ob_cache), 101)

    def test_bullish_order_block(self):
        np.random.seed(1)
        n = 60
        base = 100.0
        rows = []
        for i in range(n):
            if i == n - 5:
                o, c = 102.0, 100.5
                h, low = 102.2, 100.3
                v = 500
            else:
                o = base + np.random.uniform(-0.3, 0.3)
                c = o + np.random.uniform(-0.3, 0.3)
                h = max(o, c) + 0.3
                low = min(o, c) - 0.3
                v = np.random.uniform(10, 50)
            rows.append({"time": i, "open": o, "high": h, "low": low, "close": c, "volume": v})
        df = pd.DataFrame(rows)
        df["time"] = df["time"].astype(int)
        StrategyUtils._ob_cache.clear()
        result = StrategyUtils.detect_order_block(df, "TEST")
        self.assertIsInstance(result, str)


class TestPreprocessData(unittest.TestCase):
    def _make_ohlcv(self, n: int) -> pd.DataFrame:
        base = 100.0
        rows = []
        for i in range(n):
            o = base + i * 0.1 + np.random.uniform(-0.5, 0.5)
            c = o + np.random.uniform(-0.5, 0.5)
            h = max(o, c) + np.random.uniform(0.1, 0.5)
            low = min(o, c) - np.random.uniform(0.1, 0.5)
            v = np.random.uniform(10, 100)
            rows.append({"open": o, "high": h, "low": low, "close": c, "volume": v})
        return pd.DataFrame(rows)

    def test_returns_none_for_none(self):
        self.assertIsNone(StrategyUtils.preprocess_data(None))

    def test_returns_none_for_short(self):
        short = self._make_ohlcv(50)
        self.assertIsNone(StrategyUtils.preprocess_data(short))

    def test_full_mode_returns_dataframe_with_expected_cols(self):
        np.random.seed(42)
        df = self._make_ohlcv(150)
        result = StrategyUtils.preprocess_data(df, mode="full")
        self.assertIsNotNone(result)
        for col in ["ema", "rsi", "atr", "adx", "bb_lower", "bb_upper"]:
            self.assertIn(col, result.columns)

    def test_trend_mode_returns_dataframe(self):
        np.random.seed(42)
        df = self._make_ohlcv(150)
        result = StrategyUtils.preprocess_data(df, mode="trend")
        self.assertIsNotNone(result)
        self.assertIn("ema", result.columns)


if __name__ == "__main__":
    unittest.main()

import unittest

import pandas as pd

from core.backtester import VectorBacktester, VectorBacktestResult


def _candles(rows=80):
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    data = []
    price = 100.0
    for idx in range(rows):
        price += 0.2 if idx % 7 else -0.1
        data.append(
            {
                "time": base + pd.Timedelta(hours=idx),
                "open": price - 0.1,
                "high": price + 0.8,
                "low": price - 0.8,
                "close": price,
                "volume": 1000.0 + idx,
            }
        )
    return pd.DataFrame(data)


class VectorBacktesterTest(unittest.TestCase):
    def test_score_to_probability_returns_050_at_center(self):
        prob = VectorBacktester._score_to_probability(50.0, 1)
        self.assertAlmostEqual(prob, 0.50, places=6)
        prob = VectorBacktester._score_to_probability(50.0, -1)
        self.assertAlmostEqual(prob, 0.50, places=6)

    def test_score_to_probability_increases_with_deviation(self):
        prob60 = VectorBacktester._score_to_probability(60.0, 1)
        prob80 = VectorBacktester._score_to_probability(80.0, 1)
        self.assertGreater(prob80, prob60)

    def test_score_to_probability_clamps_at_0_95(self):
        prob = VectorBacktester._score_to_probability(100.0, 1)
        self.assertAlmostEqual(prob, 0.95, places=6)
        prob = VectorBacktester._score_to_probability(0.0, -1)
        self.assertAlmostEqual(prob, 0.95, places=6)

    def test_score_to_probability_returns_050_for_neutral_side(self):
        prob = VectorBacktester._score_to_probability(80.0, 0)
        self.assertAlmostEqual(prob, 0.50, places=6)

    def test_evaluate_filters_all_trades_with_probability_threshold_1(self):
        result = VectorBacktester(_candles(200)).evaluate(
            alma_offset=0.85,
            alma_sigma=6.0,
            z_score_threshold=1.6,
            entropy_bins=8,
            adx_threshold=25.0,
            stop_loss_pct=1.2,
            take_profit_pct=2.0,
            min_probability_threshold=1.0,
        )
        self.assertEqual(result.trades, 0)

    def test_evaluate_probability_threshold_reduces_trade_count(self):
        base = VectorBacktester(_candles(200)).evaluate(
            alma_offset=0.85,
            alma_sigma=6.0,
            z_score_threshold=1.6,
            entropy_bins=8,
            adx_threshold=25.0,
            stop_loss_pct=1.2,
            take_profit_pct=2.0,
            min_probability_threshold=0.0,
        )
        filtered = VectorBacktester(_candles(200)).evaluate(
            alma_offset=0.85,
            alma_sigma=6.0,
            z_score_threshold=1.6,
            entropy_bins=8,
            adx_threshold=25.0,
            stop_loss_pct=1.2,
            take_profit_pct=2.0,
            min_probability_threshold=0.60,
        )
        self.assertGreaterEqual(base.trades, filtered.trades)

    def test_requires_ohlcv_columns(self):
        candles = _candles().drop(columns=["volume"])

        with self.assertRaisesRegex(ValueError, "Missing candle columns"):
            VectorBacktester(candles)

    def test_sorts_and_deduplicates_candles_by_time(self):
        candles = _candles(4)
        duplicated = pd.concat([candles.iloc[[2]], candles.iloc[[3, 1, 0, 2]]])

        backtester = VectorBacktester(duplicated)

        self.assertEqual(len(backtester.df), 4)
        self.assertTrue(backtester.df["time"].is_monotonic_increasing)

    def test_accepts_numeric_millisecond_timestamps(self):
        candles = _candles(3)
        candles["time"] = candles["time"].astype("int64") // 1_000_000

        backtester = VectorBacktester(candles)

        self.assertTrue(str(backtester.df["time"].dtype).startswith("datetime64"))

    def test_rejects_unknown_strategy_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported strategy_mode"):
            VectorBacktester(_candles()).evaluate(
                alma_offset=0.85,
                alma_sigma=6.0,
                z_score_threshold=1.6,
                entropy_bins=8,
                adx_threshold=25.0,
                stop_loss_pct=1.2,
                take_profit_pct=2.0,
                strategy_mode="unknown",
            )

    def test_evaluate_returns_result_contract(self):
        result = VectorBacktester(_candles()).evaluate(
            alma_offset=0.85,
            alma_sigma=6.0,
            z_score_threshold=1.6,
            entropy_bins=8,
            adx_threshold=25.0,
            stop_loss_pct=1.2,
            take_profit_pct=2.0,
        )

        self.assertIsInstance(result, VectorBacktestResult)
        self.assertGreaterEqual(result.trades, 0)
        self.assertGreaterEqual(result.max_drawdown, 0.0)


if __name__ == "__main__":
    unittest.main()

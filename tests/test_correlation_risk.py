import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from config import Config
from core.risk.correlation_risk import (
    _pearson_correlation,
    compute_correlation_reduction,
)


def _df_from_closes(closes: list[float]) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "time": i,
                "open": float(c),
                "high": float(c) + 1.0,
                "low": float(c) - 1.0,
                "close": float(c),
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class PearsonCorrelationTests(unittest.TestCase):
    def test_identical_series_returns_one(self):
        prices = [100 + i for i in range(50)]
        corr = _pearson_correlation(prices, prices)
        self.assertAlmostEqual(corr, 1.0, places=4)

    def test_inverse_series_returns_negative_one(self):
        a = [100 + i for i in range(50)]
        b = [200 - i for i in range(50)]
        corr = _pearson_correlation(a, b)
        self.assertAlmostEqual(corr, -1.0, places=4)

    def test_constant_series_returns_zero(self):
        a = [100 + i for i in range(50)]
        b = [100.0] * 50
        corr = _pearson_correlation(a, b)
        self.assertEqual(corr, 0.0)

    def test_short_series_returns_zero(self):
        corr = _pearson_correlation([100, 101], [100, 101])
        self.assertEqual(corr, 0.0)

    def test_nan_handling(self):
        a = [float("nan")] * 10 + [100 + i for i in range(40)]
        b = [100 + i for i in range(50)]
        corr = _pearson_correlation(a, b)
        self.assertTrue(np.isnan(corr) or isinstance(corr, float))


class CorrelationReductionTests(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        self.bot.active_trades = {}

    def _make_data_service(self, frames: dict[str, pd.DataFrame]):
        ds = MagicMock()
        ds.fetch_and_update_data.side_effect = lambda sym, tf, fast_mode=True: frames.get(sym)
        self.bot.data_service = ds

    def test_disabled_returns_no_reduction(self):
        self.bot.active_trades = {"BTC/USDT": {}}
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes([100 + i for i in range(50)]),
                "BTC/USDT": _df_from_closes([100 + i for i in range(50)]),
            }
        )
        with patch.object(Config, "CORRELATION_RISK_ENABLED", False):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", ["BTC/USDT"])
        self.assertEqual(mult, 1.0)
        self.assertEqual(details, [])

    def test_no_open_trades_passes_through(self):
        self.bot.active_trades = {}
        self._make_data_service({})
        with patch.object(Config, "CORRELATION_RISK_ENABLED", True):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", [])
        self.assertEqual(mult, 1.0)
        self.assertEqual(details, [])

    def test_high_correlation_reduces_size(self):
        self.bot.active_trades = {"BTC/USDT": {}}
        correlated = [100 + i for i in range(50)]
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes(correlated),
                "BTC/USDT": _df_from_closes(correlated),
            }
        )
        with (
            patch.object(Config, "CORRELATION_RISK_ENABLED", True),
            patch.object(Config, "CORRELATION_RISK_THRESHOLD", 0.80),
        ):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", ["BTC/USDT"])
        self.assertLess(mult, 1.0)
        self.assertGreaterEqual(mult, 0.50)
        self.assertEqual(len(details), 1)
        self.assertAlmostEqual(details[0]["correlation"], 1.0, places=2)

    def test_low_correlation_no_reduction(self):
        self.bot.active_trades = {"BTC/USDT": {}}
        a = [100 + i for i in range(50)]
        b = [200 - i for i in range(50)]
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes(a),
                "BTC/USDT": _df_from_closes(b),
            }
        )
        with (
            patch.object(Config, "CORRELATION_RISK_ENABLED", True),
            patch.object(Config, "CORRELATION_RISK_THRESHOLD", 0.80),
        ):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", ["BTC/USDT"])
        self.assertEqual(mult, 1.0)
        self.assertEqual(len(details), 1)
        self.assertAlmostEqual(details[0]["correlation"], -1.0, places=2)

    def test_multiple_open_symbols_averages_correlation(self):
        self.bot.active_trades = {"BTC/USDT": {}, "SOL/USDT": {}}
        a = [100 + i for i in range(50)]
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes(a),
                "BTC/USDT": _df_from_closes(a),
                "SOL/USDT": _df_from_closes(a),
            }
        )
        with (
            patch.object(Config, "CORRELATION_RISK_ENABLED", True),
            patch.object(Config, "CORRELATION_RISK_THRESHOLD", 0.80),
        ):
            mult, details = compute_correlation_reduction(
                self.bot, "ETH/USDT", ["BTC/USDT", "SOL/USDT"]
            )
        self.assertLess(mult, 1.0)
        self.assertEqual(len(details), 2)
        for d in details:
            self.assertAlmostEqual(d["correlation"], 1.0, places=2)

    def test_single_high_positive_correlation_reduces_despite_negative_pair(self):
        self.bot.active_trades = {"BTC/USDT": {}, "SOL/USDT": {}}
        a = [100 + i for i in range(50)]
        b = [200 - i for i in range(50)]
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes(a),
                "BTC/USDT": _df_from_closes(a),
                "SOL/USDT": _df_from_closes(b),
            }
        )
        with (
            patch.object(Config, "CORRELATION_RISK_ENABLED", True),
            patch.object(Config, "CORRELATION_RISK_THRESHOLD", 0.80),
        ):
            mult, details = compute_correlation_reduction(
                self.bot, "ETH/USDT", ["BTC/USDT", "SOL/USDT"]
            )
        self.assertLess(mult, 1.0)
        self.assertEqual(len(details), 2)

    def test_no_data_service_returns_no_reduction(self):
        self.bot.active_trades = {"BTC/USDT": {}}
        self.bot.data_service = None
        with patch.object(Config, "CORRELATION_RISK_ENABLED", True):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", ["BTC/USDT"])
        self.assertEqual(mult, 1.0)

    def test_insufficient_data_returns_no_reduction(self):
        self.bot.active_trades = {"BTC/USDT": {}}
        self._make_data_service(
            {
                "ETH/USDT": _df_from_closes([100 + i for i in range(10)]),
                "BTC/USDT": _df_from_closes([100 + i for i in range(10)]),
            }
        )
        with (
            patch.object(Config, "CORRELATION_RISK_ENABLED", True),
            patch.object(Config, "CORRELATION_RISK_MIN_CANDLES", 24),
        ):
            mult, details = compute_correlation_reduction(self.bot, "ETH/USDT", ["BTC/USDT"])
        self.assertEqual(mult, 1.0)


if __name__ == "__main__":
    unittest.main()

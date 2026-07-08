import unittest

import pandas as pd

from core.candle_close_cache import CandleCloseCache
from core.strategy.utils import StrategyUtils


def _candles(start_price):
    rows = []
    price = float(start_price)
    for i in range(130):
        price += 0.1
        rows.append(
            {
                "time": 1_700_000_000_000 + i * 3_600_000,
                "open": price - 0.2,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 1_000.0 + i,
            }
        )
    return pd.DataFrame(rows)


class RuntimeSnapshotCacheTest(unittest.TestCase):
    def tearDown(self):
        StrategyUtils._candle_cache = None

    def test_runtime_snapshot_cache_isolated_by_symbol(self):
        StrategyUtils._candle_cache = CandleCloseCache()
        btc = _candles(100.0)
        dogs = _candles(0.0001)

        btc_snapshot = StrategyUtils.compute_runtime_snapshot(btc, cache_symbol="BTC/USDT")
        dogs_snapshot = StrategyUtils.compute_runtime_snapshot(dogs, cache_symbol="DOGS/USDT")

        self.assertIsNotNone(btc_snapshot)
        self.assertIsNotNone(dogs_snapshot)
        self.assertGreater(btc_snapshot["ema"], 100.0)
        self.assertGreater(btc_snapshot["ema_9"], btc_snapshot["ema_21"])
        self.assertGreater(btc_snapshot["ema_21"], 100.0)
        self.assertIn("ema_fast_spread", btc_snapshot)
        self.assertIn("ema_compression", btc_snapshot)
        self.assertIn("ema50_slope", btc_snapshot)
        self.assertLess(dogs_snapshot["ema"], 20.0)
        self.assertNotEqual(btc_snapshot["ema"], dogs_snapshot["ema"])


if __name__ == "__main__":
    unittest.main()

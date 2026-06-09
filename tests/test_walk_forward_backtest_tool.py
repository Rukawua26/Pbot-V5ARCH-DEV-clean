import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tools.walk_forward_backtest import (
    BacktestParams,
    load_candles_csv,
    run_walk_forward_backtest,
)


def _synthetic_candles(months: int = 6, rows_per_month: int = 80) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2025-01-01T00:00:00Z")
    price = 100.0
    for idx in range(months * rows_per_month):
        ts = (
            base
            + pd.Timedelta(days=idx // rows_per_month * 31)
            + pd.Timedelta(hours=idx % rows_per_month)
        )
        drift = math.sin(idx / 9.0) * 0.45 + (0.08 if (idx // rows_per_month) % 2 == 0 else -0.04)
        open_price = price
        close = max(1.0, open_price + drift)
        high = max(open_price, close) + 0.8
        low = min(open_price, close) - 0.8
        rows.append(
            {
                "time": ts.isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + idx,
            }
        )
        price = close
    return pd.DataFrame(rows)


class WalkForwardBacktestToolTest(unittest.TestCase):
    def test_load_candles_requires_ohlcv_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            pd.DataFrame({"time": ["2025-01-01"], "close": [1.0]}).to_csv(path, index=False)

            with self.assertRaises(ValueError) as ctx:
                load_candles_csv(path)

        self.assertIn("Missing candle columns", str(ctx.exception))

    def test_load_candles_accepts_parquet(self):
        candles = _synthetic_candles(months=3, rows_per_month=80)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.parquet"
            candles.to_parquet(path, index=False)

            loaded = load_candles_csv(path)

        self.assertEqual(len(loaded), len(candles))
        self.assertIn("time", loaded.columns)

    def test_run_walk_forward_backtest_returns_window_metrics(self):
        candles = _synthetic_candles()
        grid = [
            BacktestParams(
                alma_offset=0.85,
                alma_sigma=6.0,
                z_score_threshold=1.2,
                entropy_bins=8,
                adx_threshold=20.0,
                stop_loss_pct=1.0,
                take_profit_pct=1.5,
            )
        ]

        report = run_walk_forward_backtest(
            candles,
            train_months=3,
            val_months=1,
            min_windows=1,
            min_train_trades=1,
            grid=grid,
        )

        self.assertGreaterEqual(report["summary"]["windows"], 1)
        self.assertIn("avg_validation_profit_factor", report["summary"])
        self.assertIn("best_params", report["windows"][0])
        self.assertIn("validation", report["windows"][0])

    def test_run_walk_forward_backtest_rejects_short_history(self):
        candles = _synthetic_candles(months=2, rows_per_month=50)

        with self.assertRaises(ValueError) as ctx:
            run_walk_forward_backtest(candles, train_months=3, val_months=1)

        self.assertIn("At least 200 candles", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

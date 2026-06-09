import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.bot_scorecard import send_daily_exit_scorecard


class DailyScorecardTest(unittest.TestCase):
    def test_daily_scorecard_counts_all_todays_shadow_trades(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))

        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                exit_price REAL,
                pnl_percent REAL,
                mfe_percent REAL,
                reason TEXT,
                exit_reason TEXT,
                is_shadow BOOLEAN DEFAULT 0
            )
            """
        )
        conn.executemany(
            "INSERT INTO trades (timestamp, symbol, side, exit_price, pnl_percent, mfe_percent, reason, exit_reason, is_shadow) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "2026-04-27T09:00:00",
                    "AAA/USDT",
                    "BUY",
                    1.0,
                    1.2,
                    1.5,
                    "ATR_TRAILING_HIT",
                    "DYNAMIC_SL",
                    1,
                ),
                (
                    "2026-04-27T10:00:00",
                    "BBB/USDT",
                    "BUY",
                    1.0,
                    -0.4,
                    0.2,
                    "DEGRADED_CONFIDENCE_FLOOR_VIOLATED_22.1",
                    "UNKNOWN",
                    1,
                ),
                (
                    "2026-04-26T23:59:59",
                    "CCC/USDT",
                    "BUY",
                    1.0,
                    9.9,
                    2.0,
                    "ATR_TRAILING_HIT",
                    "DYNAMIC_SL",
                    1,
                ),
                (
                    "2026-04-27T11:00:00",
                    "REAL/USDT",
                    "SELL",
                    1.0,
                    0.8,
                    1.0,
                    "CLOSE",
                    "MANUAL",
                    0,
                ),
            ],
        )
        conn.commit()
        conn.close()

        bot = SimpleNamespace(
            brain=SimpleNamespace(db_name=db_path),
            breakout_agent=SimpleNamespace(watchlist={}, summary_by_source=lambda: {}),
            breakout_overrides_today=0,
            _safe_div=lambda a, b: a / b if b else 0.0,
            _calc_post_exit_drift=lambda **kwargs: None,
            log=lambda msg: None,
        )

        with patch("core.bot_scorecard.datetime") as mock_datetime:
            mock_now = __import__("datetime").datetime(2026, 4, 27, 12, 0, 0)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kwargs: __import__("datetime").datetime(
                *args, **kwargs
            )

            with patch("core.bot_scorecard.send_telegram_msg") as send_mock:
                send_daily_exit_scorecard(bot)

        message = send_mock.call_args.args[0]
        self.assertIn("Fecha: 2026-04-27", message)
        self.assertIn("Total Trades (Shadow): 2", message)
        self.assertIn("REAL HOY", message)
        self.assertIn("Trades: 1 | WR: 100.00% | PnL: +0.80%", message)
        self.assertIn("Win Rate: 50.00%", message)
        self.assertIn("4) OTRAS SALIDAS", message)
        self.assertIn("Qty: 1 | PnL: -0.40%", message)


if __name__ == "__main__":
    unittest.main()

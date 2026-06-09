import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import tools.learning as learning
from core import bot_weekly_ops
from tools.learning import Brain


def _utc_now_naive():
    return datetime.now(UTC).replace(tzinfo=None)


class WeeklyMaintenanceTest(unittest.TestCase):
    def test_weekly_maintenance_purges_shadow_and_signal_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))
            old_ts = (_utc_now_naive() - timedelta(days=45)).isoformat()
            recent_ts = (_utc_now_naive() - timedelta(days=2)).isoformat()

            conn = brain._get_conn()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS shadow_telemetry (timestamp TEXT, event_type TEXT, data TEXT)"
                )
                conn.execute(
                    "INSERT INTO shadow_telemetry (timestamp, event_type, data) VALUES (?, 'old', '{}')",
                    (old_ts,),
                )
                conn.execute(
                    "INSERT INTO shadow_telemetry (timestamp, event_type, data) VALUES (?, 'recent', '{}')",
                    (recent_ts,),
                )
                conn.execute(
                    """
                    INSERT INTO signal_alerts (ts, symbol, alert_type, execution_mode, status)
                    VALUES (?, 'BTC/USDT', 'BUY', 'PAPER', 'PENDING')
                    """,
                    (old_ts,),
                )
                conn.execute(
                    """
                    INSERT INTO signal_alerts (ts, symbol, alert_type, execution_mode, status)
                    VALUES (?, 'ETH/USDT', 'SELL', 'PAPER', 'PENDING')
                    """,
                    (recent_ts,),
                )
                conn.commit()
            finally:
                conn.close()

            result = brain.weekly_maintenance(shadow_days_to_keep=30, signal_days_to_keep=30)

            self.assertIsNone(result["error"])
            self.assertEqual(result["shadow_deleted"], 1)
            self.assertEqual(result["signal_deleted"], 1)

            conn = brain._get_conn()
            try:
                shadow_count = conn.execute("SELECT COUNT(*) FROM shadow_telemetry").fetchone()[0]
                signal_count = conn.execute("SELECT COUNT(*) FROM signal_alerts").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(shadow_count, 1)
            self.assertEqual(signal_count, 1)

    def test_check_weekly_maintenance_runs_after_friday_10_utc_once(self):
        calls = []
        logs = []

        class FakeDateTime:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 5, 22, 10, 30)

        bot = SimpleNamespace(
            _last_weekly_maintenance_utc=None,
            brain=SimpleNamespace(
                weekly_maintenance=lambda **kwargs: (
                    calls.append(kwargs)
                    or {"error": None, "shadow_deleted": 0, "signal_deleted": 0, "vacuum_ok": True}
                )
            ),
            log=logs.append,
        )

        original_datetime = bot_weekly_ops.datetime
        bot_weekly_ops.datetime = FakeDateTime
        try:
            bot_weekly_ops.check_weekly_maintenance_utc(bot)
            bot_weekly_ops.check_weekly_maintenance_utc(bot)
        finally:
            bot_weekly_ops.datetime = original_datetime

        self.assertEqual(calls, [{"shadow_days_to_keep": 30, "signal_days_to_keep": 30}])
        self.assertTrue(any("viernes 10:00 UTC" in entry for entry in logs))

    def test_rag_cache_is_limited_to_recent_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))
            original_limit = learning.RAG_CACHE_MAX_TRADES
            learning.RAG_CACHE_MAX_TRADES = 3
            brain.rag_cache_matrix = learning.np.empty((0, 8))
            brain.rag_cache_meta = []
            try:
                for idx in range(5):
                    brain.update_rag_cache(
                        {
                            "symbol": f"SYM{idx}/USDT",
                            "pnl_percent": 1.0,
                            "timestamp": str(idx),
                            "market_snapshot": {"rsi": 50, "adx": 20},
                        }
                    )
            finally:
                learning.RAG_CACHE_MAX_TRADES = original_limit

            self.assertEqual(len(brain.rag_cache_meta), 3)
            self.assertEqual(brain.rag_cache_meta[0]["symbol"], "SYM2/USDT")
            self.assertEqual(brain.rag_cache_matrix.shape[0], 3)


if __name__ == "__main__":
    unittest.main()

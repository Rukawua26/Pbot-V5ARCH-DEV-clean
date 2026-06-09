import tempfile
import unittest
from pathlib import Path

from tools.learning import Brain


class TradeContextSnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "brain.db"
        self.brain = Brain(str(self.db_path))

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_context(self, rsi=50.0, adx=20.0, atr_pct=0.01, vol_rel=1.0):
        return {
            "symbol": "BTC/USDT",
            "rsi": rsi,
            "adx": adx,
            "atr_pct": atr_pct,
            "vol_rel": vol_rel,
            "close": 50000.0,
            "ema": 49000.0,
            "trend": "RANGO",
            "z_score": 0.0,
            "spread": 0.01,
            "tier": "GOLD",
            "market_hour": 14,
            "market_breadth_dump_ratio": 0.1,
            "market_breadth_pump_ratio": 0.05,
            "btc_delta_tf": 0.0,
            "funding_rate": 0.01,
            "features_version": "v3_clean",
        }

    def test_save_trade_context_snapshot(self):
        ctx = self._fake_context()
        sid = self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="BUY",
            context_json=ctx,
            entry_timestamp="2026-01-01T00:00:00",
            is_shadow=True,
        )
        self.assertIsNotNone(sid)
        self.assertIsInstance(sid, int)

    def test_update_trade_context_result(self):
        ctx = self._fake_context()
        sid = self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="BUY",
            context_json=ctx,
            entry_timestamp="2026-01-01T00:00:00",
            is_shadow=True,
        )
        self.assertIsNotNone(sid)
        ok = self.brain.update_trade_context_result(
            trade_id=1,
            pnl_percent=2.5,
            exit_timestamp="2026-01-02T00:00:00",
            is_winner=1,
        )
        self.assertFalse(ok)

    def test_update_trade_context_result_with_matching_symbol(self):
        ctx = self._fake_context()
        sid = self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="BUY",
            context_json=ctx,
            entry_timestamp="2026-01-01T00:00:00",
            is_shadow=True,
        )
        self.assertIsNotNone(sid)
        conn = self.brain._get_conn()
        conn.execute(
            "INSERT INTO trades (symbol, side, entry_price, timestamp) "
            "VALUES ('BTC/USDT', 'BUY', 50000.0, '2026-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()
        ok = self.brain.update_trade_context_result(
            trade_id=1,
            pnl_percent=2.5,
            exit_timestamp="2026-01-02T00:00:00",
            is_winner=1,
        )
        self.assertTrue(ok)

    def test_find_similar_contexts_empty(self):
        result = self.brain.find_similar_contexts(self._fake_context(), limit=5)
        self.assertEqual(result, [])

    def test_find_similar_contexts_with_data(self):
        ctx_win = self._fake_context(rsi=60, adx=30, vol_rel=1.5)
        self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="BUY",
            context_json=ctx_win,
            entry_timestamp="2026-01-01T00:00:00",
            is_shadow=True,
        )
        conn = self.brain._get_conn()
        conn.execute(
            "UPDATE trade_context_snapshots SET pnl_percent=3.0, is_winner=1, "
            "exit_timestamp='2026-01-02T00:00:00', trade_id=1"
        )
        conn.commit()
        conn.close()

        similar = self.brain.find_similar_contexts(self._fake_context(rsi=58, adx=28), limit=5)
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0]["is_winner"], 1)

    def test_cleanup_stale_snapshots(self):
        ctx = self._fake_context()
        self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="BUY",
            context_json=ctx,
            entry_timestamp="2020-01-01T00:00:00",
            is_shadow=True,
        )
        self.brain.save_trade_context_snapshot(
            symbol="BTC/USDT",
            side="SELL",
            context_json=ctx,
            entry_timestamp="2026-01-01T00:00:00",
            is_shadow=True,
        )
        deleted = self.brain.cleanup_stale_snapshots(max_age_days=30)
        self.assertGreaterEqual(deleted, 1)

    def test_extract_similarity_vector_returns_11_features(self):
        ctx = self._fake_context()
        vec = self.brain._extract_similarity_vector(ctx)
        self.assertEqual(len(vec), 11)


if __name__ == "__main__":
    unittest.main()

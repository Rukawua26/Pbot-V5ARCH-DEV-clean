import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.train_models import (
    CURATED_VIEW,
    GHOST_RUNTIME_FEATURES,
    build_dataset,
    load_trade_rows,
)


class TrainModelsCuratedDatasetTest(unittest.TestCase):
    def _make_db(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "trades.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                pnl_percent REAL,
                market_snapshot TEXT,
                exit_reason TEXT,
                market_regime TEXT,
                entry_confidence REAL,
                is_shadow INTEGER,
                is_dirty INTEGER DEFAULT 0,
                is_adopted INTEGER DEFAULT 0
            )
            """
        )
        snapshot = json.dumps(
            {
                "rsi": 55.0,
                "adx": 22.0,
                "vol_rel": 1.2,
                "atr_pct": 0.008,
                "funding_rate": 0.001,
                "btc_delta_tf": -0.2,
                "prob_final": 72.0,
                "votos": {},
            }
        )
        rows = [
            (
                1,
                "2026-07-12T00:02:00",
                "BAD/USDT",
                "BUY",
                -45.0,
                snapshot,
                "HARD_SL",
                "RANGE",
                72.0,
                1,
                0,
                0,
            ),
            (
                2,
                "2026-07-12T00:01:00",
                "OK1/USDT",
                "BUY",
                -3.5,
                snapshot,
                "HARD_SL",
                "RANGE",
                72.0,
                1,
                0,
                0,
            ),
            (
                3,
                "2026-07-12T00:03:00",
                "OK2/USDT",
                "SELL",
                6.0,
                snapshot,
                "DYNAMIC_SL",
                "RANGE",
                80.0,
                1,
                0,
                0,
            ),
            (
                4,
                "2026-07-12T00:04:00",
                "NOISE/USDT",
                "BUY",
                0.05,
                snapshot,
                "DYNAMIC_SL",
                "RANGE",
                80.0,
                1,
                0,
                0,
            ),
            (
                5,
                "2026-07-12T00:05:00",
                "UNK/USDT",
                "BUY",
                -2.0,
                snapshot,
                "UNKNOWN",
                "RANGE",
                72.0,
                1,
                0,
                0,
            ),
            (
                6,
                "2026-07-12T00:06:00",
                "REAL/USDT",
                "BUY",
                4.0,
                snapshot,
                "DYNAMIC_SL",
                "RANGE",
                72.0,
                0,
                0,
                0,
            ),
            (
                7,
                "2026-07-12T00:07:00",
                "DIRTY/USDT",
                "BUY",
                4.0,
                snapshot,
                "DYNAMIC_SL",
                "RANGE",
                72.0,
                1,
                1,
                0,
            ),
            (
                8,
                "2026-07-12T00:08:00",
                "ADOPT/USDT",
                "BUY",
                4.0,
                snapshot,
                "DYNAMIC_SL",
                "RANGE",
                72.0,
                1,
                0,
                1,
            ),
        ]
        conn.executemany(
            """
            INSERT INTO trades
            (id, timestamp, symbol, side, pnl_percent, market_snapshot, exit_reason,
             market_regime, entry_confidence, is_shadow, is_dirty, is_adopted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return db_path

    def test_load_trade_rows_creates_curated_view_and_filters_training_noise(self):
        db_path = self._make_db()

        rows, filtered_noise = load_trade_rows(db_path, min_abs_pnl=0.10, max_abs_pnl=10.0)

        self.assertEqual([row["symbol"] for row in rows], ["OK1/USDT", "OK2/USDT"])
        self.assertGreaterEqual(filtered_noise, 3)
        conn = sqlite3.connect(db_path)
        try:
            view_exists = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name=?",
                (CURATED_VIEW,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(view_exists, 1)

    def test_build_dataset_uses_runtime_ghost_features_only(self):
        db_path = self._make_db()
        rows, filtered_noise = load_trade_rows(db_path, min_abs_pnl=0.10, max_abs_pnl=10.0)

        bundle = build_dataset(rows, filtered_noise)

        self.assertEqual(bundle.x_ghost.columns.tolist(), GHOST_RUNTIME_FEATURES)
        self.assertEqual(bundle.bootstrap_prob.tolist(), [0.72, 0.72])
        self.assertEqual(bundle.y_class.tolist(), [0, 1])


if __name__ == "__main__":
    unittest.main()

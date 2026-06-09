import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tools.shadow_delta_report import (
    WindowMetrics,
    _classify_veto_reason,
    _compute_drawdown_from_returns,
    build_window_metrics,
    summarize_execution_events,
    summarize_log,
    summarize_trades,
)


class ShadowDeltaReportTests(unittest.TestCase):
    def test_classify_veto_reason(self):
        self.assertEqual(_classify_veto_reason("❌ VETO: BAJA PROB IA (44.0%)"), "low_prob")
        self.assertEqual(_classify_veto_reason("⛔ VETO: VETO_KAVA: RIESGO EXCESIVO"), "kava")
        self.assertEqual(_classify_veto_reason("⛔ VETO: SHOCK DEMASIADO CERCA"), "shock")
        self.assertEqual(_classify_veto_reason("⛔ VETO: MTF_VETO: MTF_VETO_15M"), "mtf")
        self.assertEqual(_classify_veto_reason("⛔ VETO: OTRA COSA"), "other")

    def test_compute_drawdown_from_returns(self):
        drawdown = _compute_drawdown_from_returns([10.0, -5.0, -10.0, 8.0])
        self.assertGreater(drawdown, 0.0)

    def test_summarize_log_counts_verdicts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sniper.log"
            path.write_text(
                "\n".join(
                    [
                        "2026-05-14 10:00:00 | 🔎 AAA/USDT: signal=BUY prob=52.0 verdict=👻 SHADOW (IA 52.0% | 50-74%)",
                        "2026-05-14 10:01:00 | 🔎 BBB/USDT: signal=BUY prob=49.0 verdict=❌ VETO: BAJA PROB IA (49.0%)",
                        "2026-05-14 10:02:00 | 🔎 CCC/USDT: signal=BUY prob=45.0 verdict=⛔ VETO: VETO_KAVA: RIESGO EXCESIVO",
                    ]
                ),
                encoding="utf-8",
            )
            start = datetime(2026, 5, 14, 9, 59, tzinfo=UTC)
            end = datetime(2026, 5, 14, 10, 3, tzinfo=UTC)
            summary = summarize_log(path, start, end)
            self.assertEqual(summary["signals_total"], 3)
            self.assertEqual(summary["verdict_shadow"], 1)
            self.assertEqual(summary["verdict_veto"], 2)
            self.assertEqual(summary["veto_low_prob"], 1)
            self.assertEqual(summary["veto_kava"], 1)

    def test_summarize_execution_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "execution_events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts":"2026-05-14T10:00:00+00:00","event":"ORDER_INTENT_CREATED","payload":{}}',
                        '{"ts":"2026-05-14T10:00:01+00:00","event":"ORDER_FILLED","payload":{}}',
                        '{"ts":"2026-05-14T10:00:02+00:00","event":"IGNORED","payload":{}}',
                    ]
                ),
                encoding="utf-8",
            )
            start = datetime(2026, 5, 14, 9, 59, tzinfo=UTC)
            end = datetime(2026, 5, 14, 10, 3, tzinfo=UTC)
            summary = summarize_execution_events(path, start, end)
            self.assertEqual(summary["order_intents"], 1)
            self.assertEqual(summary["order_filled"], 1)

    def test_summarize_trades_excludes_system_and_computes_quality(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "trades.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE trades (
                    symbol TEXT,
                    timestamp TEXT,
                    open_time TEXT,
                    pnl_percent REAL,
                    mae_percent REAL,
                    mfe_percent REAL,
                    reason TEXT,
                    is_shadow INTEGER
                )
                """
            )
            conn.executemany(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "AAA/USDT",
                        "2026-05-14T10:30:00+00:00",
                        "2026-05-14T10:00:00+00:00",
                        1.0,
                        -0.5,
                        2.0,
                        "ATR_TRAILING_HIT",
                        1,
                    ),
                    (
                        "BBB/USDT",
                        "2026-05-14T11:30:00+00:00",
                        "2026-05-14T11:00:00+00:00",
                        -0.5,
                        -1.2,
                        0.8,
                        "DEGRADED_CONFIDENCE_FLOOR_VIOLATED_39.4",
                        1,
                    ),
                    (
                        "SYSTEM",
                        "2026-05-14T11:31:00+00:00",
                        None,
                        -99.0,
                        None,
                        None,
                        None,
                        1,
                    ),
                ],
            )
            conn.commit()
            conn.close()

            start = datetime(2026, 5, 14, 9, 0, tzinfo=UTC)
            end = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
            summary = summarize_trades(db_path, start, end)
            self.assertEqual(summary["shadow_entries"], 2)
            self.assertEqual(summary["shadow_closed"], 2)
            self.assertAlmostEqual(summary["win_rate_pct"], 50.0)
            self.assertAlmostEqual(summary["expectancy_pct"], 0.25)
            self.assertAlmostEqual(summary["profit_factor"], 2.0)
            self.assertAlmostEqual(summary["net_pnl_pct"], 0.5)
            self.assertAlmostEqual(summary["degraded_rate_pct"], 50.0)

    def test_build_window_metrics_combines_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_path = root / "sniper.log"
            log_path.write_text(
                "2026-05-14 10:00:00 | 🔎 AAA/USDT: signal=BUY prob=52.0 verdict=👻 SHADOW (IA 52.0% | 50-74%)\n",
                encoding="utf-8",
            )
            events_path = root / "execution_events.jsonl"
            events_path.write_text(
                '{"ts":"2026-05-14T10:00:00+00:00","event":"ORDER_INTENT_CREATED","payload":{}}\n',
                encoding="utf-8",
            )
            db_path = root / "trades.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE trades (
                    symbol TEXT,
                    timestamp TEXT,
                    open_time TEXT,
                    pnl_percent REAL,
                    mae_percent REAL,
                    mfe_percent REAL,
                    reason TEXT,
                    is_shadow INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "AAA/USDT",
                    "2026-05-14T10:30:00+00:00",
                    "2026-05-14T10:00:00+00:00",
                    1.0,
                    -0.5,
                    1.5,
                    "ATR_TRAILING_HIT",
                    1,
                ),
            )
            conn.commit()
            conn.close()

            start = datetime(2026, 5, 14, 9, 30, tzinfo=UTC)
            end = datetime(2026, 5, 14, 11, 0, tzinfo=UTC)
            metrics = build_window_metrics(
                label="current",
                start=start,
                end=end,
                log_path=log_path,
                events_path=events_path,
                db_path=db_path,
            )
            self.assertIsInstance(metrics, WindowMetrics)
            self.assertEqual(metrics.signals_total, 1)
            self.assertEqual(metrics.verdict_shadow, 1)
            self.assertEqual(metrics.order_intents, 1)
            self.assertEqual(metrics.shadow_entries, 1)
            self.assertEqual(metrics.shadow_closed, 1)


if __name__ == "__main__":
    unittest.main()

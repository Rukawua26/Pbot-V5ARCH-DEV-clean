import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.intelligence.collector import collect_runtime_dataset
from tools.intelligence.loaders import load_execution_events, load_state_snapshot
from tools.intelligence.report_builder import (
    build_and_store_advisories,
    build_daily_report,
    build_postmortem_report,
    build_weekly_report,
)
from tools.intelligence.storage import (
    ensure_intelligence_tables,
    fetch_trade_annotations,
    list_advisory_snapshots,
)
from tools.learning import Brain


class IntelligenceLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "brain.db"
        self.events_path = self.root / "execution_events.jsonl"
        self.state_path = self.root / "sniper_state.json"
        self.brain = Brain(str(self.db_path))
        ensure_intelligence_tables(self.db_path)

        conn = self.brain._get_conn()
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, pnl, pnl_percent, reason, is_shadow, market_regime, entry_confidence, exit_confidence, mae_percent, mfe_percent, open_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-06-14T10:00:00+00:00",
                "BTC/USDT",
                "BUY",
                12.0,
                2.4,
                "TP_HIT",
                1,
                "BULL",
                72.0,
                68.0,
                -0.8,
                3.1,
                "2026-06-14T09:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, pnl, pnl_percent, reason, is_shadow, market_regime, entry_confidence, exit_confidence, mae_percent, mfe_percent, open_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-06-14T11:00:00+00:00",
                "ETH/USDT",
                "SELL",
                -8.0,
                -1.6,
                "STOP_LOSS",
                0,
                "BEAR",
                61.0,
                44.0,
                -1.9,
                0.7,
                "2026-06-14T10:30:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        self.events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-06-14T10:05:00+00:00",
                            "event": "FILTER_APPLIED",
                            "payload": {
                                "symbol": "BTC/USDT",
                                "side": "BUY",
                                "filter_passed": False,
                                "filter_reason": "OI_DELTA_VETO",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-06-14T10:15:00+00:00",
                            "event": "RANGE_VETO",
                            "payload": {
                                "symbol": "ETH/USDT",
                                "side": "SELL",
                                "btc_regime": "RANGE",
                            },
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        self.state_path.write_text(
            json.dumps(
                {
                    "ts": 1718359200.0,
                    "mode": "PAPER",
                    "balance": 1000.0,
                    "available_balance": 950.0,
                    "halt_system_active": False,
                    "circuit_breaker_active": False,
                    "active_trades_count": 1,
                    "shadow_trades_count": 1,
                    "regime": "BULL_TREND",
                    "sentiment": "GREED",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_loaders_handle_snapshot_and_events(self):
        state = load_state_snapshot(self.state_path)
        events = load_execution_events(self.events_path, limit=10)
        self.assertIsNotNone(state)
        self.assertEqual(state.mode, "PAPER")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event, "FILTER_APPLIED")

    def test_collect_runtime_dataset_generates_annotations_and_uses_shadow(self):
        dataset = collect_runtime_dataset(
            db_path=self.db_path,
            events_path=self.events_path,
            state_path=self.state_path,
            hours=24 * 365,
        )
        self.assertEqual(dataset["summary"]["trade_count"], 2)
        self.assertEqual(dataset["summary"]["shadow_trade_count"], 1)
        self.assertEqual(dataset["research"]["shadow_vs_real"]["shadow"]["trades"], 1)
        annotations = fetch_trade_annotations(self.db_path, limit=10)
        self.assertEqual(len(annotations), 2)

    def test_reports_and_advisories_are_generated(self):
        dataset = collect_runtime_dataset(
            db_path=self.db_path,
            events_path=self.events_path,
            state_path=self.state_path,
            hours=24 * 365,
        )
        daily = build_daily_report(dataset)
        weekly = build_weekly_report(dataset)
        with patch("tools.intelligence.storage.REPORTS_DIR", self.root / "reports"):
            advisories = build_and_store_advisories(dataset, db_path=self.db_path)
        self.assertEqual(daily["report_type"], "daily")
        self.assertEqual(weekly["report_type"], "weekly")
        self.assertGreaterEqual(len(advisories), 1)
        stored = list_advisory_snapshots(self.db_path, limit=10)
        self.assertGreaterEqual(len(stored), 1)

    def test_postmortem_marks_shadow_as_consultive_signal(self):
        collect_runtime_dataset(
            db_path=self.db_path,
            events_path=self.events_path,
            state_path=self.state_path,
            hours=24 * 365,
        )
        report = build_postmortem_report(1, db_path=self.db_path)
        self.assertIsNotNone(report)
        self.assertEqual(report["mode"], "SHADOW")
        self.assertTrue(any("SHADOW" in note for note in report["advice"]))

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import shadow_validation
from tools.shadow_validation_report import build_summary, render_markdown


class ShadowValidationMetricTests(unittest.TestCase):
    def test_filter_decision_payload_is_observational(self):
        with (
            patch.object(shadow_validation.Config, "SHADOW_VALIDATION_ENABLED", True),
            patch.object(shadow_validation.Config, "SHADOW_VALIDATION_CAMPAIGN", "test-campaign"),
            patch("core.shadow_validation.append_runtime_metric") as append_metric,
        ):
            shadow_validation.emit_filter_decision(
                "BTC/USDT",
                "BUY",
                False,
                "FEAR_10_VETO",
                72.5,
                {
                    "fear_greed_index": 10,
                    "btc_dominance": 66.5,
                    "agent_signal_override": True,
                    "agent_direction_score": 24.0,
                },
            )

        append_metric.assert_called_once()
        metric, payload = append_metric.call_args.args
        self.assertEqual(metric, "shadow_validation")
        self.assertEqual(payload["campaign"], "test-campaign")
        self.assertEqual(payload["event"], "filter_decision")
        self.assertEqual(payload["symbol"], "BTC/USDT")
        self.assertFalse(payload["filter_passed"])
        self.assertTrue(payload["agent_signal_override"])

    def test_shadow_trade_closed_only_records_shadow(self):
        with (
            patch.object(shadow_validation.Config, "SHADOW_VALIDATION_ENABLED", True),
            patch("core.shadow_validation.append_runtime_metric") as append_metric,
        ):
            shadow_validation.emit_shadow_trade_closed(
                {"symbol": "BTC/USDT", "is_shadow": False},
                "MANUAL",
                100.0,
                1.0,
                1.0,
                -0.5,
                2.0,
                "MANUAL",
            )
            shadow_validation.emit_shadow_trade_closed(
                {"symbol": "ETH/USDT", "side": "SELL", "is_shadow": True, "entry": 110.0},
                "ATR_TRAILING_HIT",
                100.0,
                2.0,
                1.8,
                -0.2,
                3.5,
                "ATR_TRAILING_HIT",
            )

        append_metric.assert_called_once()
        payload = append_metric.call_args.args[1]
        self.assertEqual(payload["event"], "shadow_trade_closed")
        self.assertEqual(payload["symbol"], "ETH/USDT")
        self.assertEqual(payload["pnl_percent"], 1.8)


class ShadowValidationReportTests(unittest.TestCase):
    def test_report_summarizes_runtime_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime_metrics.jsonl"
            records = [
                {
                    "metric": "shadow_validation",
                    "payload": {
                        "event": "filter_decision",
                        "filter_passed": False,
                        "filter_reason": "FEAR_10_VETO",
                        "agent_signal_override": True,
                    },
                },
                {
                    "metric": "shadow_validation",
                    "payload": {
                        "event": "filter_decision",
                        "filter_passed": True,
                        "macro_boost_reason": "BTC_DOM=66.5%",
                    },
                },
                {
                    "metric": "shadow_validation",
                    "payload": {
                        "event": "shadow_trade_closed",
                        "pnl_percent": 2.0,
                        "pnl_usd": 1.2,
                        "mae_percent": -0.5,
                        "mfe_percent": 3.0,
                    },
                },
                {
                    "metric": "shadow_validation",
                    "payload": {
                        "event": "shadow_trade_closed",
                        "pnl_percent": -1.0,
                        "pnl_usd": -0.8,
                        "mae_percent": -1.5,
                        "mfe_percent": 0.7,
                    },
                },
                {
                    "metric": "shadow_validation",
                    "payload": {
                        "event": "fvg_cycle",
                        "new_gaps": 3,
                        "active_total": 5,
                        "status_counts": {"ACTIVE": 4, "FILLED": 1},
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

            summary = build_summary(path)

        self.assertEqual(summary["filters"]["total"], 2)
        self.assertEqual(summary["filters"]["veto_rate_pct"], 50.0)
        self.assertEqual(summary["filters"]["macro_vetoes"], 1)
        self.assertEqual(summary["filters"]["macro_boosts"], 1)
        self.assertEqual(summary["filters"]["agent_override_rate_pct"], 50.0)
        self.assertEqual(summary["shadow_trades"]["closed"], 2)
        self.assertEqual(summary["shadow_trades"]["winrate_pct"], 50.0)
        self.assertEqual(summary["shadow_trades"]["total_pnl_usd"], 0.4)
        self.assertEqual(summary["fvg"]["new_gaps"], 3)
        self.assertIn("SHADOW Validation Report", render_markdown(summary))


if __name__ == "__main__":
    unittest.main()

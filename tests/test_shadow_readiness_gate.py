import unittest

from tools.shadow_readiness_gate import (
    evaluate_shadow_readiness,
    filter_rows_since,
    filter_rows_since_ts,
    summarize_execution_events,
    summarize_runtime,
)


class ShadowReadinessGateTests(unittest.TestCase):
    def test_summarize_execution_events_counts_rates(self):
        rows = [
            {"event": "ORDER_INTENT_CREATED"},
            {"event": "ORDER_INTENT_CREATED"},
            {"event": "ORDER_FILLED"},
            {"event": "ENTRY_ACK_UNKNOWN_PERSISTED"},
        ]

        summary = summarize_execution_events(rows)

        self.assertEqual(summary["order_intents"], 2)
        self.assertEqual(summary["order_filled"], 1)
        self.assertEqual(summary["entry_ack_unknown"], 1)
        self.assertAlmostEqual(summary["entry_ack_unknown_rate"], 0.5)

    def test_evaluate_shadow_readiness_passes_with_clean_inputs(self):
        failures = evaluate_shadow_readiness(
            {
                "counts": {"ORDER_INTENT_CREATED": 20, "ORDER_FILLED": 12},
                "order_intents": 20,
                "order_filled": 12,
                "entry_ack_unknown": 0,
                "entry_ack_unknown_rate": 0.0,
            },
            {"samples": 40, "rss_max": 500.0, "cpu_max": 55.0, "guardian_busy_max": 40.0},
            {
                "halt_system_active": False,
                "integrity_lock_active": False,
                "circuit_breaker_active": False,
                "is_paused": False,
            },
            min_runtime_samples=30,
            min_filled_orders=10,
            max_ack_unknown_rate=0.02,
            max_rss_mb=800.0,
            max_cpu_pct=95.0,
            max_guardian_busy_pct=95.0,
        )

        self.assertEqual(failures, [])

    def test_evaluate_shadow_readiness_fails_on_critical_events_and_health_flags(self):
        failures = evaluate_shadow_readiness(
            {
                "counts": {
                    "ORDER_INTENT_CREATED": 5,
                    "ORDER_FILLED": 2,
                    "FAIL_SAFE_CLOSE_FAILED_HALT": 1,
                },
                "order_intents": 5,
                "order_filled": 2,
                "entry_ack_unknown": 1,
                "entry_ack_unknown_rate": 0.2,
            },
            {"samples": 4, "rss_max": 900.0, "cpu_max": 99.0, "guardian_busy_max": 96.0},
            {
                "halt_system_active": True,
                "integrity_lock_active": False,
                "circuit_breaker_active": False,
                "is_paused": False,
            },
            min_runtime_samples=30,
            min_filled_orders=10,
            max_ack_unknown_rate=0.02,
            max_rss_mb=800.0,
            max_cpu_pct=95.0,
            max_guardian_busy_pct=95.0,
        )

        self.assertTrue(any("FAIL_SAFE_CLOSE_FAILED_HALT" in item for item in failures))
        self.assertTrue(any("halt_system_active=true" in item for item in failures))
        self.assertTrue(any("ENTRY_ACK_UNKNOWN rate" in item for item in failures))
        self.assertTrue(any("runtime_metrics samples" in item for item in failures))

    def test_summarize_runtime_handles_empty_input(self):
        summary = summarize_runtime([])
        self.assertEqual(summary["samples"], 0)
        self.assertIsNone(summary["rss_max"])

    def test_filter_rows_since_keeps_recent_and_invalid_timestamps(self):
        rows = [
            {"ts": "2099-01-01T00:00:00+00:00", "event": "RECENT"},
            {"ts": "2000-01-01T00:00:00+00:00", "event": "OLD"},
            {"ts": "not-a-date", "event": "UNKNOWN"},
        ]

        filtered = filter_rows_since(rows, 24.0)

        self.assertEqual([row["event"] for row in filtered], ["RECENT", "UNKNOWN"])

    def test_filter_rows_since_ts_keeps_only_events_from_marker(self):
        rows = [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "OLD"},
            {"ts": "2026-01-02T00:00:00+00:00", "event": "NEW"},
            {"ts": "invalid", "event": "UNKNOWN"},
        ]

        filtered = filter_rows_since_ts(
            rows, __import__("datetime").datetime.fromisoformat("2026-01-01T12:00:00+00:00")
        )

        self.assertEqual([row["event"] for row in filtered], ["NEW", "UNKNOWN"])


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path

from core.runtime_metrics import append_runtime_metric
from tools.runtime_metrics_summary import summarize_runtime_metrics


class RuntimeMetricsTest(unittest.TestCase):
    def test_runtime_metrics_summary_counts_exchange_calls_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            old = os.environ.get("SNIPER_DISABLE_FILE_TELEMETRY")
            try:
                os.chdir(tmp)
                os.environ["SNIPER_DISABLE_FILE_TELEMETRY"] = "0"
                append_runtime_metric(
                    "exchange_call",
                    {"op": "fetch_ticker", "ok": True, "latency_ms": 12.5},
                )
                append_runtime_metric(
                    "exchange_call",
                    {
                        "op": "fetch_ticker",
                        "ok": False,
                        "latency_ms": 20.0,
                        "error_type": "ExchangeNotAvailable",
                    },
                )
                append_runtime_metric("halt", {"reason": "TEST"})
                summary = summarize_runtime_metrics(Path("logs/runtime_metrics.jsonl"))
            finally:
                if old is None:
                    os.environ.pop("SNIPER_DISABLE_FILE_TELEMETRY", None)
                else:
                    os.environ["SNIPER_DISABLE_FILE_TELEMETRY"] = old
                os.chdir(cwd)

        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["metrics"]["halt"], 1)
        self.assertEqual(summary["exchange_calls"]["fetch_ticker"]["count"], 2)
        self.assertEqual(summary["errors"]["ExchangeNotAvailable"], 1)

    def test_runtime_metrics_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            old = os.environ.get("SNIPER_DISABLE_FILE_TELEMETRY")
            try:
                os.chdir(tmp)
                os.environ["SNIPER_DISABLE_FILE_TELEMETRY"] = "1"
                append_runtime_metric("exchange_call", {"op": "fetch_balance", "ok": True})
                self.assertFalse(Path("logs/runtime_metrics.jsonl").exists())
            finally:
                if old is None:
                    os.environ.pop("SNIPER_DISABLE_FILE_TELEMETRY", None)
                else:
                    os.environ["SNIPER_DISABLE_FILE_TELEMETRY"] = old
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()

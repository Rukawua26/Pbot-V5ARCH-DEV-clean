import unittest
from unittest.mock import MagicMock, patch

from config import Config


class TestAgentWeightMonitor(unittest.TestCase):
    """Tests for core/strategy/weight_monitor.py"""

    def setUp(self):
        self.brain_mock = MagicMock()
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 100.0,
            "SR": 100.0,
            "G": 100.0,
        }

    def test_evaluate_returns_empty_when_no_degradation(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        # Prime history with stable scores
        for _ in range(Config.AGENT_MIN_TRADES_BEFORE_ALERT):
            monitor.evaluate()
        reports = monitor.evaluate()
        self.assertEqual(reports, [])

    def test_evaluate_detects_degradation(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        # Build history with high scores
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 140.0,
            "SR": 100.0,
            "G": 100.0,
        }
        for _ in range(Config.AGENT_MIN_TRADES_BEFORE_ALERT):
            monitor.evaluate()
        # Now drop MT
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 60.0,
            "SR": 100.0,
            "G": 100.0,
        }
        reports = monitor.evaluate()
        mt_reports = [r for r in reports if r["agent"] == "MT"]
        self.assertGreater(len(mt_reports), 0)
        self.assertGreater(mt_reports[0]["drop_pct"], 0)

    def test_evaluate_skips_insufficient_data(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        # Only 1 evaluation, not enough to alert
        reports = monitor.evaluate()
        self.assertEqual(reports, [])

    def test_alert_sent_on_degradation(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        # Prime
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 140.0,
            "SR": 100.0,
            "G": 100.0,
        }
        for _ in range(Config.AGENT_MIN_TRADES_BEFORE_ALERT):
            monitor.evaluate()
        # Degrade
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 60.0,
            "SR": 100.0,
            "G": 100.0,
        }
        with patch("core.strategy.weight_monitor.send_telegram_msg") as mock_send:
            with patch("core.strategy.weight_monitor.append_execution_event"):
                reports = monitor.run_check()
                self.assertGreater(len(reports), 0)
                mock_send.assert_called()

    def test_deduplication_suppresses_repeated_alerts(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        # Prime with high scores
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 140.0,
            "SR": 100.0,
            "G": 100.0,
        }
        for _ in range(Config.AGENT_MIN_TRADES_BEFORE_ALERT):
            monitor.evaluate()
        # Degrade and alert
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 60.0,
            "SR": 100.0,
            "G": 100.0,
        }
        with patch("core.strategy.weight_monitor.send_telegram_msg") as mock_send:
            with patch("core.strategy.weight_monitor.append_execution_event"):
                monitor.run_check()
                first_call_count = mock_send.call_count
                # Run again with same score - should NOT alert (dedup)
                monitor.run_check()
                self.assertEqual(mock_send.call_count, first_call_count)

    def test_run_check_passes_bot_to_append_event(self):
        from core.strategy.weight_monitor import AgentWeightMonitor

        monitor = AgentWeightMonitor(self.brain_mock)
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 140.0,
            "SR": 100.0,
            "G": 100.0,
        }
        for _ in range(Config.AGENT_MIN_TRADES_BEFORE_ALERT):
            monitor.evaluate()
        self.brain_mock.get_agent_performance.return_value = {
            "MT": 60.0,
            "SR": 100.0,
            "G": 100.0,
        }
        bot_mock = MagicMock()
        with patch("core.strategy.weight_monitor.send_telegram_msg"):
            with patch("core.strategy.weight_monitor.append_execution_event") as mock_append:
                monitor.run_check(bot=bot_mock)
                for call in mock_append.call_args_list:
                    self.assertEqual(call[0][0], bot_mock)
                    self.assertEqual(call[0][1], "AGENT_WEIGHT_MONITOR")


if __name__ == "__main__":
    unittest.main()

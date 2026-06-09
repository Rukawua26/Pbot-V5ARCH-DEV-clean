import unittest
from unittest.mock import MagicMock, patch

from config import Config


class TestMTFVetoStats(unittest.TestCase):
    """Tests for MTF rolling veto rate metrics in filter.py"""

    def setUp(self):
        # Reset module-level counters before each test
        import core.signals.mtf.filter as mtf_filter

        mtf_filter._MTF_TOTAL_ATTEMPTS = 0
        mtf_filter._MTF_VETOED_COUNT = 0
        mtf_filter._MTF_VETO_REASONS = {}

    @patch("core.signals.mtf.filter.fetch_mtf_data")
    @patch("core.signals.mtf.filter.analyze_mtf_alignment")
    def test_metrics_track_attempts_and_vetos(self, mock_analyze, mock_fetch):
        mock_fetch.return_value = {"15m": MagicMock(), "5m": MagicMock()}
        mock_analyze.return_value = (0.0, "STRONG_CONFLICT_15M")

        from core.signals.mtf.filter import apply_mtf_filter

        ctx = {}

        # Override Config.MTF_FILTER_ENABLED to True
        with patch.object(Config, "MTF_FILTER_ENABLED", True):
            with patch.object(Config, "MTF_METRICS_WINDOW", 100):
                apply_mtf_filter(MagicMock(), "BTCUSDT", "BUY", 75.0, ctx, MagicMock())

        import core.signals.mtf.filter as mtf_filter

        self.assertEqual(mtf_filter._MTF_TOTAL_ATTEMPTS, 1)
        self.assertEqual(mtf_filter._MTF_VETOED_COUNT, 1)
        self.assertIn("STRONG_CONFLICT_15M", mtf_filter._MTF_VETO_REASONS)

    @patch("core.signals.mtf.filter.fetch_mtf_data")
    @patch("core.signals.mtf.filter.analyze_mtf_alignment")
    def test_metrics_log_and_reset_at_window(self, mock_analyze, mock_fetch):
        mock_fetch.return_value = {"15m": MagicMock(), "5m": MagicMock()}
        mock_analyze.return_value = (0.95, "ALIGNED")

        from core.signals.mtf.filter import apply_mtf_filter

        # Set window to 3 for quick test
        with patch.object(Config, "MTF_FILTER_ENABLED", True):
            with patch.object(Config, "MTF_METRICS_WINDOW", 3):
                with patch("core.signals.mtf.filter.append_execution_event"):
                    for _ in range(3):
                        apply_mtf_filter(MagicMock(), "ETHUSDT", "SELL", 65.0, {}, MagicMock())

        import core.signals.mtf.filter as mtf_filter

        # After 3 attempts (window=3), counters should reset
        self.assertEqual(mtf_filter._MTF_TOTAL_ATTEMPTS, 0)

    @patch("core.signals.mtf.filter.fetch_mtf_data")
    @patch("core.signals.mtf.filter.analyze_mtf_alignment")
    def test_per_reason_breakdown(self, mock_analyze, mock_fetch):
        mock_fetch.return_value = {"15m": MagicMock(), "5m": MagicMock()}
        mock_analyze.side_effect = [
            (0.0, "CONFLICT_15M"),
            (0.0, "CONFLICT_15M"),
            (0.0, "CONFLICT_5M"),
            (0.95, "ALIGNED"),
        ]

        from core.signals.mtf.filter import apply_mtf_filter

        with patch.object(Config, "MTF_FILTER_ENABLED", True):
            with patch.object(Config, "MTF_METRICS_WINDOW", 100):
                for _ in range(4):
                    apply_mtf_filter(MagicMock(), "BTCUSDT", "BUY", 75.0, {}, MagicMock())

        import core.signals.mtf.filter as mtf_filter

        self.assertEqual(mtf_filter._MTF_VETO_REASONS.get("CONFLICT_15M"), 2)
        self.assertEqual(mtf_filter._MTF_VETO_REASONS.get("CONFLICT_5M"), 1)


class TestMTFWinRateTracking(unittest.TestCase):
    """Tests for MTF win-rate tracking in trade_exit.py"""

    def setUp(self):
        import core.trade_exit as te

        te._MTF_TRADE_RESULTS = []

    def test_record_mtf_trade_skips_non_mtf(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        trade = {"market_snapshot": {"votos": {"MT": 80}}}
        _record_mtf_trade_outcome(trade, 2.0)
        self.assertEqual(len(_MTF_TRADE_RESULTS), 0)

    def test_record_mtf_trade_win(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        trade = {"market_snapshot": {"mtf_reason": "ALIGNED", "votos": {"MT": 80}}}
        _record_mtf_trade_outcome(trade, 2.5)
        self.assertEqual(len(_MTF_TRADE_RESULTS), 1)
        self.assertTrue(_MTF_TRADE_RESULTS[0]["is_win"])
        self.assertEqual(_MTF_TRADE_RESULTS[0]["mtf_reason"], "ALIGNED")

    def test_record_mtf_trade_loss(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        trade = {"market_snapshot": {"mtf_reason": "CONFLICT_15M", "votos": {"MT": 80}}}
        _record_mtf_trade_outcome(trade, -3.0)
        self.assertEqual(len(_MTF_TRADE_RESULTS), 1)
        self.assertFalse(_MTF_TRADE_RESULTS[0]["is_win"])

    def test_window_triggers_report(self):
        import core.trade_exit as te
        from core.trade_exit import _record_mtf_trade_outcome

        with patch.object(Config, "MTF_METRICS_WINDOW", 3):
            with patch("core.trade_exit.append_execution_event"):
                for i in range(3):
                    trade = {"market_snapshot": {"mtf_reason": f"REASON_{i}", "votos": {"MT": 80}}}
                    _record_mtf_trade_outcome(trade, 1.0)

        # After window hit, report was generated and list was reset (check via module)
        self.assertEqual(len(te._MTF_TRADE_RESULTS), 0)


if __name__ == "__main__":
    unittest.main()

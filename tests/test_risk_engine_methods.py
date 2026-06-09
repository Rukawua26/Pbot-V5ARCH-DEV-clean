import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestCalculatePositionSizeByStop(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                with patch("core.risk_engine.Config") as mock_config:
                    mock_config.MAX_MARGIN_PERCENT = 5.0
                    mock_config.MAX_RISK_USD = 100.0
                    mock_config.RISK_PER_TRADE_PCT = 0.01
                    from core.risk_engine import RiskEngine

                    self.engine = RiskEngine(brain=MagicMock())

    def test_returns_zero_when_balance_zero(self):
        amount, notional = self.engine.calculate_position_size_by_stop(
            0.0, "BTC/USDT", 100.0, 90.0, 10
        )
        self.assertEqual(amount, 0.0)
        self.assertEqual(notional, -2)

    def test_returns_zero_when_entry_zero(self):
        amount, notional = self.engine.calculate_position_size_by_stop(
            1000.0, "BTC/USDT", 0.0, 90.0, 10
        )
        self.assertEqual(amount, 0.0)

    def test_returns_zero_when_stop_zero(self):
        amount, notional = self.engine.calculate_position_size_by_stop(
            1000.0, "BTC/USDT", 100.0, 0.0, 10
        )
        self.assertEqual(amount, 0.0)

    def test_returns_zero_when_stop_distance_zero(self):
        amount, notional = self.engine.calculate_position_size_by_stop(
            1000.0, "BTC/USDT", 100.0, 100.0, 10
        )
        self.assertEqual(amount, 0.0)
        self.assertEqual(notional, -5)

    def test_calculates_correct_amount(self):
        # balance=1000, risk_pct=0.01 → risk_budget=$10
        # stop_distance = 100 - 90 = 10
        # raw_amount = 10 / 10 = 1.0
        amount, notional = self.engine.calculate_position_size_by_stop(
            1000.0, "BTC/USDT", 100.0, 90.0, 10
        )
        self.assertGreater(amount, 0)
        self.assertEqual(notional, amount * 100.0)

    def test_respects_max_risk_usd(self):
        with patch("core.risk_engine.Config") as mock_config:
            mock_config.MAX_RISK_USD = 5.0
            mock_config.RISK_PER_TRADE_PCT = 0.05
            mock_config.MAX_MARGIN_PERCENT = 5.0

            # balance=1000, risk_pct=0.05 → risk_budget=$50
            # But MAX_RISK_USD = 5, so should be capped
            amount, notional = self.engine.calculate_position_size_by_stop(
                1000.0, "BTC/USDT", 100.0, 90.0, 10
            )
            # risk_budget should be capped at 5
            expected_risk = 5.0
            expected_amount = expected_risk / 10.0
            self.assertAlmostEqual(amount, expected_amount, delta=0.01)


class TestCheckAntiRevengeBlacklist(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())
                self.engine.symbol_streaks = {}
                self.engine.temp_blacklist = {}

    def test_returns_true_when_safe(self):
        """Returns (True, 'SAFE') when symbol is not blacklisted."""
        is_safe, reason = self.engine.check_anti_revenge_blacklist("BTC/USDT")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "SAFE")

    def test_returns_false_when_blacklisted(self):
        """Returns (False, reason) when symbol is in blacklist and not expired."""
        self.engine.temp_blacklist["BTC/USDT"] = datetime.now() + timedelta(hours=1)
        is_safe, reason = self.engine.check_anti_revenge_blacklist("BTC/USDT")
        self.assertFalse(is_safe)
        self.assertIn("ANTI_REVENGE_BLACKLIST", reason)

    def test_clears_expired_blacklist(self):
        """Expired blacklist entries are cleared and return (True, 'SAFE')."""
        self.engine.temp_blacklist["BTC/USDT"] = datetime.now() - timedelta(hours=1)
        self.engine.symbol_streaks["BTC/USDT"] = 3
        is_safe, reason = self.engine.check_anti_revenge_blacklist("BTC/USDT")
        self.assertTrue(is_safe)
        self.assertNotIn("BTC/USDT", self.engine.temp_blacklist)
        self.assertEqual(self.engine.symbol_streaks["BTC/USDT"], 0)


class TestRecordTradeResult(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())
                self.engine.symbol_streaks = {}
                self.engine.temp_blacklist = {}

    def test_records_win_resets_streak(self):
        self.engine.symbol_streaks["BTC/USDT"] = 2
        self.engine.record_trade_result("BTC/USDT", 5.0)  # positive pnl
        self.assertEqual(self.engine.symbol_streaks["BTC/USDT"], 0)

    def test_records_loss_increases_streak(self):
        self.engine.record_trade_result("BTC/USDT", -5.0)  # negative pnl
        self.assertEqual(self.engine.symbol_streaks["BTC/USDT"], 1)

    def test_records_loss_multiple_times(self):
        self.engine.record_trade_result("BTC/USDT", -1.0)
        self.engine.record_trade_result("BTC/USDT", -2.0)
        self.assertEqual(self.engine.symbol_streaks["BTC/USDT"], 2)

    def test_blacklists_after_two_losses(self):
        self.engine.record_trade_result("BTC/USDT", -1.0)
        self.assertNotIn("BTC/USDT", self.engine.temp_blacklist)
        self.engine.record_trade_result("BTC/USDT", -2.0)
        self.assertIn("BTC/USDT", self.engine.temp_blacklist)


class TestCheckSignalIntegrity(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())

    def test_returns_false_when_integrity_ok(self):
        trade = {"entry_confidence": 80.0, "side": "BUY"}
        is_degraded, reason = self.engine.check_signal_integrity(trade, 75.0, 2.0)
        self.assertFalse(is_degraded)
        self.assertEqual(reason, "INTEGRITY_OK")

    def test_detects_confidence_floor_violation_long(self):
        trade = {"entry_confidence": 80.0, "side": "BUY"}
        is_degraded, reason = self.engine.check_signal_integrity(trade, 50.0, 1.0)
        self.assertTrue(is_degraded)
        self.assertIn("CONFIDENCE_FLOOR_VIOLATED", reason)

    def test_detects_sudden_confidence_crash_long(self):
        # Score drop > 30% within 3 mins: (80-50)/80 = 37.5% > 30%
        # But current_ai_score=50 < 52, so CONFIDENCE_FLOOR fires first
        # Let's use a score where floor doesn't fire: current > 52
        trade2 = {"entry_confidence": 80.0, "side": "BUY"}
        is_degraded, reason = self.engine.check_signal_integrity(trade2, 55.0, 2.0)
        # score_drop = (80-55)/80 = 31.25% > 30% and elapsed <= 3
        self.assertTrue(is_degraded)
        self.assertIn("SUDDEN_CONFIDENCE_CRASH", reason)

    def test_detects_short_thesis_invalidated(self):
        trade = {"entry_confidence": 80.0, "side": "SELL"}
        is_degraded, reason = self.engine.check_signal_integrity(trade, 60.0, 1.0)
        self.assertTrue(is_degraded)
        self.assertIn("SHORT_THESIS_INVALIDATED", reason)


class TestShouldAbortTrade(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())

    def test_does_not_abort_when_confidence_high(self):
        should_abort, reason = self.engine.should_abort_trade(80.0, 75.0)
        self.assertFalse(should_abort)

    def test_aborts_when_confidence_drops_below_threshold(self):
        # 50 < 80 * 0.70 = 56
        should_abort, reason = self.engine.should_abort_trade(80.0, 50.0)
        self.assertTrue(should_abort)
        self.assertIn("CONF_DEGRADED", reason)

    def test_uses_custom_threshold_factor(self):
        should_abort, reason = self.engine.should_abort_trade(80.0, 50.0, threshold_factor=0.60)
        # 50 < 80 * 0.60 = 48 → False
        self.assertFalse(should_abort)


if __name__ == "__main__":
    unittest.main()

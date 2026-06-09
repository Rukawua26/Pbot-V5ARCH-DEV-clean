import unittest
from unittest.mock import MagicMock

from core.trade_manager import _calculate_pnl_and_metrics


class TestCalculatePnlAndMetrics(unittest.TestCase):
    def test_buy_position_profit(self):
        trade = {"amount": 1.0, "entry": 100.0}
        exit_price = 110.0
        fees = 1.0
        result = _calculate_pnl_and_metrics(trade, exit_price, fees, "BUY")
        # pnl_bruto = (110 - 100) * 1.0 = 10
        # pnl_neto = 10 - 1 = 9
        self.assertAlmostEqual(result["pnl_bruto_usd"], 10.0, delta=0.01)
        self.assertAlmostEqual(result["pnl_neto_usd"], 9.0, delta=0.01)
        self.assertAlmostEqual(result["pnl_neto_percent"], (9 / 100.0) * 100, delta=0.1)

    def test_buy_position_loss(self):
        trade = {"amount": 2.0, "entry": 100.0}
        exit_price = 95.0
        fees = 0.5
        result = _calculate_pnl_and_metrics(trade, exit_price, fees, "BUY")
        # pnl_bruto = (95 - 100) * 2.0 = -10
        # pnl_neto = -10 - 0.5 = -10.5
        self.assertAlmostEqual(result["pnl_bruto_usd"], -10.0, delta=0.01)
        self.assertAlmostEqual(result["pnl_neto_usd"], -10.5, delta=0.01)

    def test_sell_position_profit(self):
        trade = {"amount": 1.0, "entry": 100.0}
        exit_price = 90.0
        fees = 0.5
        result = _calculate_pnl_and_metrics(trade, exit_price, fees, "SELL")
        # pnl_bruto = (100 - 90) * 1.0 = 10 (SELL: inverted)
        # pnl_neto = 10 - 0.5 = 9.5
        self.assertAlmostEqual(result["pnl_bruto_usd"], 10.0, delta=0.01)
        self.assertAlmostEqual(result["pnl_neto_usd"], 9.5, delta=0.01)

    def test_sell_position_loss(self):
        trade = {"amount": 1.0, "entry": 100.0}
        exit_price = 110.0
        fees = 0.5
        result = _calculate_pnl_and_metrics(trade, exit_price, fees, "SELL")
        # pnl_bruto = (100 - 110) * 1.0 = -10
        # pnl_neto = -10 - 0.5 = -10.5
        self.assertAlmostEqual(result["pnl_bruto_usd"], -10.0, delta=0.01)
        self.assertAlmostEqual(result["pnl_neto_usd"], -10.5, delta=0.01)

    def test_calculates_mae_mfe_for_buy(self):
        trade = {"amount": 1.0, "entry": 100.0, "mae_price": 95.0, "mfe_price": 110.0}
        result = _calculate_pnl_and_metrics(trade, 105.0, 0.0, "BUY")
        # MAE: (100 - 95) / 100 * 100 = 5%
        self.assertAlmostEqual(result["mae_percent"], 5.0, delta=0.1)
        # MFE: (110 - 100) / 100 * 100 = 10%
        self.assertAlmostEqual(result["mfe_percent"], 10.0, delta=0.1)

    def test_calculates_mae_mfe_for_sell(self):
        trade = {"amount": 1.0, "entry": 100.0, "mae_price": 110.0, "mfe_price": 90.0}
        result = _calculate_pnl_and_metrics(trade, 105.0, 0.0, "SELL")
        # MAE for SELL: (110 - 100) / 100 * 100 = 10%
        self.assertAlmostEqual(result["mae_percent"], 10.0, delta=0.1)
        # MFE for SELL: (100 - 90) / 100 * 100 = 10%
        self.assertAlmostEqual(result["mfe_percent"], 10.0, delta=0.1)

    def test_returns_zero_when_val_zero(self):
        trade = {"amount": 1.0, "entry": 0.0}
        result = _calculate_pnl_and_metrics(trade, 100.0, 0.0, "BUY")
        self.assertEqual(result["pnl_neto_percent"], 0.0)


class TestSafeLogSignalAlert(unittest.TestCase):
    def test_calls_brain_method_when_available(self):
        from core.trade_manager import _safe_log_signal_alert

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.brain.log_signal_alert = MagicMock()
        bot.db_lock = None

        _safe_log_signal_alert(bot, key1="value1")
        bot.brain.log_signal_alert.assert_called_once_with(key1="value1")

    def test_does_nothing_when_brain_missing(self):
        from core.trade_manager import _safe_log_signal_alert

        bot = MagicMock()
        bot.brain = None

        # Should not raise
        _safe_log_signal_alert(bot, key1="value1")

    def test_does_nothing_when_method_not_callable(self):
        from core.trade_manager import _safe_log_signal_alert

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.brain.log_signal_alert = "not_callable"
        bot.db_lock = None

        # Should not raise
        _safe_log_signal_alert(bot, key1="value1")


class TestSafeUpdateSignalAlertStatus(unittest.TestCase):
    def test_calls_brain_method_when_available(self):
        from core.trade_manager import _safe_update_signal_alert_status

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.brain.update_signal_alert_status = MagicMock()
        bot.db_lock = None

        _safe_update_signal_alert_status(bot, "cid_123", "FILLED")
        bot.brain.update_signal_alert_status.assert_called_once_with("cid_123", "FILLED")

    def test_does_nothing_when_method_not_callable(self):
        from core.trade_manager import _safe_update_signal_alert_status

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.brain.update_signal_alert_status = "not_callable"

        # Should not raise
        _safe_update_signal_alert_status(bot, "cid_123", "FILLED")


if __name__ == "__main__":
    unittest.main()

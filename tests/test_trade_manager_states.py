import unittest
from unittest.mock import MagicMock, PropertyMock, patch


class TestGetLocalOpenTradeCounts(unittest.TestCase):
    def test_returns_zero_when_no_active_trades(self):
        from core.trade_manager import _get_local_open_trade_counts

        bot = MagicMock()
        bot.active_trades = {}
        bot.log = MagicMock()
        max_real, max_shadow = _get_local_open_trade_counts(bot)
        self.assertEqual(max_real, 0)

    def test_counts_open_trades_correctly(self):
        from core.trade_manager import _get_local_open_trade_counts

        bot = MagicMock()
        bot.active_trades = {
            "BTC/USDT": {"status": "OPEN"},
            "ETH/USDT": {"status": "FILLED"},
        }
        bot.log = MagicMock()
        bot.brain = MagicMock()
        bot.brain.load_active_trade_states.return_value = {}
        max_real, max_shadow = _get_local_open_trade_counts(bot)
        self.assertEqual(max_real, 1)  # Only BTC is OPEN

    def test_returns_defaults_on_exception(self):
        from core.trade_manager import _get_local_open_trade_counts

        bot = MagicMock()
        # Make active_trades raise an exception when accessed
        type(bot).active_trades = PropertyMock(side_effect=Exception("error"))
        bot.log = MagicMock()
        max_real, max_shadow = _get_local_open_trade_counts(bot)
        # Should return defaults from Config
        self.assertGreaterEqual(max_real, 1)


class TestExchangePositionIsFlat(unittest.TestCase):
    @patch("core.trade_entry.normalize_position_symbol")
    def test_returns_true_when_no_positions(self, mock_norm):
        from core.trade_manager import _exchange_position_is_flat

        bot = MagicMock()
        bot.execution.fetch_positions.return_value = []
        mock_norm.return_value = "BTC/USDT"
        result = _exchange_position_is_flat(bot, "BTC/USDT")
        self.assertTrue(result)

    @patch("core.trade_entry.normalize_position_symbol")
    def test_returns_false_when_position_open(self, mock_norm):
        from core.trade_manager import _exchange_position_is_flat

        bot = MagicMock()
        bot.execution.fetch_positions.return_value = [{"symbol": "BTC/USDT", "contracts": 1.0}]
        mock_norm.return_value = "BTC/USDT"
        result = _exchange_position_is_flat(bot, "BTC/USDT")
        self.assertFalse(result)

    def test_raises_when_fetch_positions_unavailable(self):
        from core.trade_manager import _exchange_position_is_flat

        bot = MagicMock()
        bot.execution = MagicMock()
        del bot.execution.fetch_positions
        with self.assertRaises(RuntimeError):
            _exchange_position_is_flat(bot, "BTC/USDT")


class TestSafeLogSignalAlert(unittest.TestCase):
    def test_calls_method_without_lock(self):
        from core.trade_manager import _safe_log_signal_alert

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.db_lock = None
        _safe_log_signal_alert(bot, signal="test", score=99.0)
        bot.brain.log_signal_alert.assert_called_once()

    def test_skips_when_method_not_callable(self):
        from core.trade_manager import _safe_log_signal_alert

        bot = MagicMock()
        bot.brain = MagicMock()
        bot.brain.log_signal_alert = "not_callable"
        bot.db_lock = None
        # Should not raise
        _safe_log_signal_alert(bot, signal="test")


class TestValidateEntryPreconditionsExtended(unittest.TestCase):
    def _make_bot(self, **kwargs):
        bot = MagicMock()
        bot.stop_requested = kwargs.get("stop", False)
        bot.shutdown_in_progress = kwargs.get("shutdown", False)
        bot.active_trades = kwargs.get("trades", {})
        bot.integrity_lock_active = kwargs.get("integrity", False)
        bot.halt_system_active = kwargs.get("halt", False)
        bot.confidence_stagnation_lock_active = kwargs.get("stagnation", False)
        bot.log = MagicMock()
        return bot

    @patch("core.trade_entry.shadow_logger")
    def test_returns_halted_when_shadow_logger_halted(self, mock_shadow):
        from core.trade_manager import _validate_entry_preconditions

        mock_shadow.is_trading_halted.return_value = True
        bot = self._make_bot()
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "TRADING_HALTED_DB_ERROR")

    def test_returns_halt_system_active(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = self._make_bot(halt=True)
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "HALT_SYSTEM_ACTIVE")

    def test_returns_stagnation_lock(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = self._make_bot(stagnation=True)
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "CONFIDENCE_STAGNATION_LOCK")


if __name__ == "__main__":
    unittest.main()

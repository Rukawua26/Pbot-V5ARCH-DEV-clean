import unittest
from unittest.mock import MagicMock, patch


class TestClampLeverage(unittest.TestCase):
    """Tests for _clamp_leverage_1_to_10 helper function."""

    def test_import_function(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        self.assertTrue(callable(_clamp_leverage_1_to_10))

    def test_clamps_zero_to_one(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(0)
        self.assertEqual(result, 1)

    def test_clamps_negative_to_one(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(-5)
        self.assertEqual(result, 1)

    def test_clamps_above_ten_to_ten(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(50)
        self.assertEqual(result, 10)

    def test_leave_valid_leverage_unchanged(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        for lev in [1, 5, 10]:
            self.assertEqual(_clamp_leverage_1_to_10(lev), lev)

    def test_handles_float_input(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(7.5)
        self.assertEqual(result, 7)

    def test_handles_invalid_string(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10("invalid")
        self.assertEqual(result, 10)

    def test_handles_none(self):
        from core.trade_manager import _clamp_leverage_1_to_10

        result = _clamp_leverage_1_to_10(None)
        self.assertEqual(result, 10)


class TestFailSafeCloseWhenSlMissing(unittest.TestCase):
    """Tests for _fail_safe_close_when_sl_missing with mocked bot."""

    def _make_bot(self, close_effect=None, market_effect=None):
        bot = MagicMock()
        if close_effect:
            bot.execution.close_position.side_effect = close_effect
        else:
            bot.execution.close_position.return_value = True
        if market_effect:
            bot.execution.create_reduce_only_market_order.side_effect = market_effect
        else:
            bot.execution.create_reduce_only_market_order.return_value = True
        bot.log = MagicMock()
        return bot

    def test_returns_true_on_success_first_attempt(self):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = self._make_bot()
        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)
        self.assertTrue(result)
        bot.execution.close_position.assert_called_once_with("BTC/USDT", "BUY", 0.1)

    def test_returns_true_on_market_fallback(self):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = Exception("chase limit fail")
        bot.execution.create_reduce_only_market_order.return_value = True
        bot.log = MagicMock()
        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)
        self.assertTrue(result)
        self.assertEqual(bot.execution.close_position.call_count, 3)
        bot.execution.create_reduce_only_market_order.assert_called_once()

    @patch("time.sleep", return_value=None)
    def test_retries_chase_then_market(self, mock_sleep):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = [Exception("fail1"), Exception("fail2"), True]
        bot.execution.create_reduce_only_market_order.return_value = True
        bot.log = MagicMock()

        result = _fail_safe_close_when_sl_missing(bot, "ETH/USDT", "SELL", 0.05)
        self.assertTrue(result)
        self.assertEqual(bot.execution.close_position.call_count, 3)
        bot.execution.create_reduce_only_market_order.assert_not_called()

    @patch("time.sleep", return_value=None)
    def test_returns_false_after_all_attempts_fail(self, mock_sleep):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = Exception("persistent chase fail")
        bot.execution.create_reduce_only_market_order.side_effect = Exception(
            "persistent market fail"
        )
        bot.log = MagicMock()

        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)
        self.assertFalse(result)
        self.assertEqual(bot.execution.close_position.call_count, 3)
        self.assertEqual(bot.execution.create_reduce_only_market_order.call_count, 2)

    @patch("time.sleep", return_value=None)
    def test_logs_each_failure(self, mock_sleep):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = Exception("e")
        bot.execution.create_reduce_only_market_order.side_effect = Exception("e")
        bot.log = MagicMock()

        _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)
        self.assertEqual(bot.log.call_count, 7)


class TestValidateEntryPreconditions(unittest.TestCase):
    """Tests for _validate_entry_preconditions function."""

    def test_returns_shutdown_when_stop_requested(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = True
        bot.shutdown_in_progress = False
        bot.active_trades = {}
        bot.log = MagicMock()

        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "SHUTDOWN_IN_PROGRESS")

    def test_returns_shutdown_when_shutdown_in_progress(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = False
        bot.shutdown_in_progress = True
        bot.active_trades = {}
        bot.log = MagicMock()

        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "SHUTDOWN_IN_PROGRESS")

    def test_returns_recovery_pending_when_state_pending(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = False
        bot.shutdown_in_progress = False
        bot.active_trades = {"BTC/USDT": {"status": "PENDING_SEND"}}
        bot.log = MagicMock()

        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "RECOVERY_PENDING_STATE")

    def test_returns_trading_halted_when_shadow_logger_halted(self):
        from core.trade_manager import _validate_entry_preconditions

        with patch("core.trade_entry.shadow_logger") as mock_shadow:
            mock_shadow.is_trading_halted.return_value = True
            bot = MagicMock()
            bot.stop_requested = False
            bot.shutdown_in_progress = False
            bot.active_trades = {}
            bot.log = MagicMock()

            result = _validate_entry_preconditions(bot, "BTC/USDT", False)
            self.assertEqual(result, "TRADING_HALTED_DB_ERROR")

    def test_returns_integrity_lock_when_active(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = False
        bot.shutdown_in_progress = False
        bot.active_trades = {}
        bot.integrity_lock_active = True
        bot.log = MagicMock()

        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "INTEGRITY_LOCK_ACTIVE")

    def test_returns_none_when_all_ok(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = False
        bot.shutdown_in_progress = False
        bot.active_trades = {}
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.confidence_stagnation_lock_active = False
        bot.log = MagicMock()

        result = _validate_entry_preconditions(bot, "BTC/USDT", True)
        self.assertIsNone(result)


class TestModuleAvailable(unittest.TestCase):
    """Tests for _module_available helper."""

    def test_returns_true_for_builtin_module(self):
        from core.trade_manager import _module_available

        self.assertTrue(_module_available("sys"))

    def test_returns_false_for_nonexistent_module(self):
        from core.trade_manager import _module_available

        self.assertFalse(_module_available("this_module_does_not_exist_12345"))


if __name__ == "__main__":
    unittest.main()

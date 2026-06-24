import threading
import unittest
from types import SimpleNamespace
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
            bot.execution.close_position.return_value = {"status": "closed"}
        if market_effect:
            bot.execution.create_reduce_only_market_order.side_effect = market_effect
        else:
            bot.execution.create_reduce_only_market_order.return_value = {"status": "closed"}
        bot.execution.fetch_positions.return_value = []
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
        bot.execution.create_reduce_only_market_order.return_value = {"status": "closed"}
        bot.execution.fetch_positions.return_value = []
        bot.log = MagicMock()
        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)
        self.assertTrue(result)
        self.assertEqual(bot.execution.close_position.call_count, 3)
        bot.execution.create_reduce_only_market_order.assert_called_once_with(
            "BTC/USDT", "SELL", 0.1
        )

    def test_market_fallback_uses_buy_to_close_short(self):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = Exception("chase limit fail")
        bot.execution.create_reduce_only_market_order.return_value = {"status": "closed"}
        bot.execution.fetch_positions.return_value = []
        bot.log = MagicMock()

        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "SELL", 0.1)

        self.assertTrue(result)
        bot.execution.create_reduce_only_market_order.assert_called_once_with(
            "BTC/USDT", "BUY", 0.1
        )

    @patch("time.sleep", return_value=None)
    def test_returns_false_on_ambiguous_close_result(self, mock_sleep):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.return_value = {"exit_state": "STUCK", "status": "open"}
        bot.execution.create_reduce_only_market_order.return_value = {"status": "open"}
        bot.execution.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.1, "side": "long"}
        ]
        bot.log = MagicMock()

        result = _fail_safe_close_when_sl_missing(bot, "BTC/USDT", "BUY", 0.1)

        self.assertFalse(result)

    @patch("time.sleep", return_value=None)
    def test_retries_chase_then_market(self, mock_sleep):
        from core.trade_manager import _fail_safe_close_when_sl_missing

        bot = MagicMock()
        bot.execution.close_position.side_effect = [
            Exception("fail1"),
            Exception("fail2"),
            {"status": "closed"},
        ]
        bot.execution.create_reduce_only_market_order.return_value = {"status": "closed"}
        bot.execution.fetch_positions.return_value = []
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
        bot.execution.fetch_positions.return_value = []
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
        bot.execution.fetch_positions.return_value = []
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


class TestRealCloseFailureHalt(unittest.TestCase):
    """REAL close failure should activate HALT and EXIT_STUCK."""

    def _make_bot(self):
        return SimpleNamespace(
            lock=threading.Lock(),
            db_lock=threading.Lock(),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            closing_in_progress=False,
            active_trades={},
            log=MagicMock(),
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {"symbol": "BTC/USDT:USDT", "contracts": 0.1, "side": "long"}
                ],
                close_position=MagicMock(side_effect=RuntimeError("network timeout")),
            ),
            brain=SimpleNamespace(
                save_active_trade_state=MagicMock(return_value=True),
                save_error_snapshot=MagicMock(),
            ),
        )

    @patch("core.trade_exit.Config.PAPER_MODE", False)
    @patch("core.trade_exit.send_telegram_msg")
    def test_real_close_failure_sets_halt_and_exit_stuck(self, mock_tg):
        from core.trade_exit import close_trade

        bot = self._make_bot()
        bot.active_trades["BTC/USDT"] = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "entry": 100.0,
            "amount": 0.1,
            "is_shadow": False,
            "closing_in_progress": False,
            "status": "OPEN",
        }

        close_trade(bot, "BTC/USDT", "TEST_EXIT", 99.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        status = bot.active_trades["BTC/USDT"].get("status")
        self.assertEqual(status, "EXIT_STUCK")


class TestTp1FailureNoStateReduction(unittest.TestCase):
    """TP1 partial close failure should not reduce local state."""

    def _make_bot(self, tp_order_result=None, raise_on_tp=False):
        bot = SimpleNamespace(
            lock=threading.Lock(),
            db_lock=threading.Lock(),
            price_lock=threading.Lock(),
            is_running=True,
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            active_trades={},
            log=MagicMock(),
            live_prices={},
            execution=SimpleNamespace(
                fetch_ticker=MagicMock(return_value={"last": 102.0}),
                place_hard_sl=MagicMock(return_value={"id": "sl-1"}),
                create_reduce_only_market_order=(
                    MagicMock(return_value=tp_order_result)
                    if not raise_on_tp
                    else MagicMock(side_effect=RuntimeError("min notional"))
                ),
            ),
            sync_wallet=MagicMock(),
            _guardian_stats={"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0},
            _exit_eval_last_log={},
            exit_engine=SimpleNamespace(
                evaluate_exit=MagicMock(return_value={"should_exit": False})
            ),
            risk_engine=SimpleNamespace(
                should_abort_trade=MagicMock(return_value=(False, "OK")),
                should_defer_confidence_exit_for_fee_noise=MagicMock(return_value=(False, "OK")),
            ),
            monitor_open_trades=MagicMock(),
            brain=SimpleNamespace(
                pending_model_update=False,
                upsert_confidence_exit_audit=MagicMock(return_value=1),
            ),
        )
        bot.monitor_open_trades.side_effect = lambda: setattr(bot, "is_running", False)
        return bot

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_tp1_failure_does_not_reduce_size_usd(self, _mock_sleep):
        from core.bot_guardian import run_guardian_loop

        bot = self._make_bot(tp_order_result=None, raise_on_tp=True)
        bot.active_trades["TEST/USDT"] = {
            "symbol": "TEST/USDT",
            "side": "BUY",
            "entry": 100.0,
            "amount": 1.0,
            "sl": 98.0,
            "tp": 0.0,
            "pnl": 2.0,
            "peak_pnl": 2.0,
            "open_time": "2024-01-01T00:00:00Z",
            "is_shadow": False,
            "trailing_active": False,
        }
        bot.live_prices["TESTUSDT"] = 102.0

        run_guardian_loop(bot)

        trade = bot.active_trades.get("TEST/USDT", {})
        self.assertEqual(trade.get("amount"), 1.0)
        self.assertNotIn("tp1_triggered", trade)


class TestHardSlVerificationFailure(unittest.TestCase):
    """HARD SL verification failure in REAL should activate HALT."""

    def _make_bot(self, fetch_fails=False):
        return SimpleNamespace(
            lock=threading.Lock(),
            db_lock=threading.Lock(),
            log=MagicMock(),
            integrity_lock_active=False,
            is_paused=False,
            halt_system_active=False,
            active_trades={},
            brain=SimpleNamespace(
                save_error_snapshot=MagicMock(),
            ),
            execution=SimpleNamespace(
                fetch_open_orders=lambda: (
                    (_ for _ in ()).throw(RuntimeError("network down"))
                    if fetch_fails
                    else [
                        {
                            "id": "sl-1",
                            "type": "STOP_MARKET",
                            "side": "sell",
                            "amount": 1.0,
                            "info": {},
                        }
                    ]
                ),
            ),
        )

    @patch("core.bot_wallet_sync.Config.PAPER_MODE", False)
    def test_verification_failure_halts_real_trade(self):
        from core.bot_wallet_sync import _find_verified_hard_sl_order

        bot = self._make_bot(fetch_fails=True)
        trade = {"symbol": "TEST/USDT", "side": "BUY", "amount": 1.0, "is_shadow": False}

        result = _find_verified_hard_sl_order(bot, "TEST/USDT", trade, {})

        self.assertEqual(result, "HALT")
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)


class TestPlaceHardSlRequiresClientIdInReal(unittest.TestCase):
    """place_hard_sl must reject None client_order_id in REAL mode."""

    @patch("core.execution_service.Config.PAPER_MODE", False)
    def test_real_mode_requires_client_order_id(self):
        from core.execution_service import ExecutionService

        service = ExecutionService("k", "s")
        service.exchange = SimpleNamespace(
            price_to_precision=lambda _s, p: str(p),
            create_order=lambda *a, **k: {"id": "mock"},
        )
        service.set_weight_tracker(None)

        result = service.place_hard_sl("TEST/USDT", "BUY", 1.0, 99.0, client_order_id=None)

        self.assertIsNone(result)
        self.assertIn("client_order_id", str(service.last_hard_sl_error).lower())


if __name__ == "__main__":
    unittest.main()

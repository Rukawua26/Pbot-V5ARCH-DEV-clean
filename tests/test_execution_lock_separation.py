import threading
import unittest
from unittest.mock import MagicMock, patch

from core.execution_service import ExecutionService


class ExecutionLockSeparationTest(unittest.TestCase):
    """Verify exchange call lock separation: _account_lock vs _exchange_call_lock."""

    def setUp(self):
        self.service = ExecutionService.__new__(ExecutionService)
        self.service.logger = MagicMock()
        self.service.weight_tracker = None
        self.service._exchange_call_lock = threading.RLock()
        self.service._account_lock = threading.RLock()
        self.service.exchange = MagicMock()
        self.service._last_valid_balance = None
        self.service._cancel_all_failures = {}
        self.service._cancel_all_failure_events = {}
        self.service._symbol_quarantine_until = {}
        self.service._no_price_exit_state = {}
        self.service._no_price_exit_daily_metrics = {}
        self.service.last_hard_sl_error = ""
        self.service.last_entry_reject_error = ""

    # --- Lock identity ---

    def test_two_locks_are_different_instances(self):
        self.assertIsNot(self.service._exchange_call_lock, self.service._account_lock)

    def test_locks_are_independent(self):
        acquired = {"account": False, "exchange": False}

        def acquire_account():
            with self.service._account_lock:
                acquired["account"] = True

        def acquire_exchange():
            with self.service._exchange_call_lock:
                acquired["exchange"] = True

        with self.service._exchange_call_lock:
            t = threading.Thread(target=acquire_account)
            t.start()
            t.join(timeout=2)
            self.assertTrue(acquired["account"])

        acquired = {"account": False, "exchange": False}

        with self.service._account_lock:
            t = threading.Thread(target=acquire_exchange)
            t.start()
            t.join(timeout=2)
            self.assertTrue(acquired["exchange"])

    # --- _call_exchange uses exchange_call_lock ---

    def test_call_exchange_uses_exchange_lock(self):
        self.service.exchange.fetch_balance = MagicMock(return_value={"total": {"USDT": 100}})
        lock_held = {"inside_fn": False, "after_release": False}

        def check_lock():
            lock_held["inside_fn"] = self.service._exchange_call_lock._is_owned()

        self.service._call_exchange("test_op", check_lock)
        self.assertTrue(
            lock_held["inside_fn"], "exchange lock should be owned inside _call_exchange callback"
        )

    def test_call_exchange_account_uses_account_lock(self):
        self.service.exchange.fetch_balance = MagicMock(return_value={"total": {"USDT": 100}})
        lock_held = {"inside_fn": False}

        def check_lock():
            lock_held["inside_fn"] = self.service._account_lock._is_owned()

        self.service._call_exchange_account("test_acc_op", check_lock)
        self.assertTrue(
            lock_held["inside_fn"],
            "account lock should be owned inside _call_exchange_account callback",
        )

    def test_call_exchange_account_does_not_hold_exchange_lock(self):
        self.service.exchange.fetch_balance = MagicMock(return_value={"total": {"USDT": 100}})
        exchange_held = {"during": False}

        def check_locks():
            exchange_held["during"] = self.service._exchange_call_lock._is_owned()

        self.service._call_exchange_account("test_acc_op", check_locks)
        self.assertFalse(
            exchange_held["during"],
            "exchange lock should NOT be held inside _call_exchange_account (uses _no_lock=True)",
        )

    def test_call_exchange_account_delegates_with_no_lock(self):
        self.service.exchange.fetch_balance = MagicMock(return_value={"total": {"USDT": 100}})
        no_lock_value = {"value": None}

        original = self.service._call_exchange

        def tracking(op_name, fn, *, retries=2, timeout_s=0.0, _no_lock=False):
            no_lock_value["value"] = _no_lock
            return original(op_name, fn, retries=retries, timeout_s=timeout_s, _no_lock=_no_lock)

        self.service._call_exchange = tracking
        self.service._call_exchange_account("op", lambda: self.service.exchange.fetch_balance())
        self.assertTrue(no_lock_value["value"])

    # --- Account-level methods use _call_exchange_account ---

    def test_wait_order_filled_uses_account_lock(self):
        self.service.exchange.fetch_order = MagicMock(return_value={"status": "filled"})

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"status": "filled"}
            self.service._wait_order_filled("BTC/USDT", "order-1", timeout_s=5)
            mock_acc.assert_called()

    def test_confirm_ioc_order_state_uses_account_lock(self):
        self.service.exchange.fetch_order = MagicMock(
            return_value={"status": "filled", "filled": 1.0}
        )

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"status": "filled", "filled": 1.0}
            result = self.service._confirm_ioc_order_state(
                "BTC/USDT", {"id": "ioc-1", "status": "open", "filled": "0"}, None
            )
            self.assertIsNotNone(result)
            mock_acc.assert_called()

    def test_fetch_open_interest_uses_account_lock(self):
        self.service.exchange.fetch_open_interest = MagicMock(
            return_value={"openInterest": "100.5"}
        )

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"openInterest": "100.5"}
            self.service.fetch_open_interest("BTC/USDT")
            mock_acc.assert_called()

    def test_fetch_balance_uses_account_lock(self):
        self.service.exchange.fetch_balance = MagicMock(return_value={"total": {"USDT": 100.0}})

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"total": {"USDT": 100.0}}
            self.service.fetch_balance()
            mock_acc.assert_called()

    def test_fetch_positions_uses_account_lock(self):
        self.service.exchange.fetch_positions = MagicMock(return_value=[])

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = []
            self.service.fetch_positions()
            mock_acc.assert_called()

    def test_fetch_open_orders_uses_account_lock(self):
        self.service.exchange.fetch_open_orders = MagicMock(return_value=[{"id": "o1"}])

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = [{"id": "o1"}]
            self.service.fetch_open_orders("BTC/USDT")
            mock_acc.assert_called()

    def test_fetch_order_by_client_id_uses_account_lock(self):
        self.service.exchange.market_id = MagicMock(return_value="BTCUSDT")

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {
                "orderId": "1",
                "status": "FILLED",
                "clientOrderId": "cid-1",
                "executedQty": "1",
                "origQty": "1",
                "avgPrice": "100",
                "price": "100",
            }
            self.service.fetch_order_by_client_id("BTC/USDT", "cid-1")
            mock_acc.assert_called()

    def test_fetch_my_trades_uses_account_lock(self):
        self.service.exchange.fetch_my_trades = MagicMock(return_value=[])

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = []
            self.service.fetch_my_trades("BTC/USDT")
            mock_acc.assert_called()

    def test_get_balance_uses_account_lock(self):
        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"total": {"USDT": 100.0}, "info": {}}
            self.assertEqual(self.service.get_balance(), 100.0)
            mock_acc.assert_called()

    def test_close_position_chase_fetch_ticker_uses_account_lock(self):
        self.service.exchange.fetch_ticker = MagicMock(return_value={"last": 50000.0})
        self.service.exchange.cancel_all_orders = MagicMock()
        self.service.exchange.price_to_precision = MagicMock(return_value=50000.0)

        with patch.object(self.service, "_call_exchange_account") as mock_acc:
            mock_acc.return_value = {"last": 50000.0}
            with patch(
                "core.execution_service._execute_chase_limit_steps",
                return_value={"exit_state": "FILLED"},
            ):
                self.service._close_position_chase("BTC/USDT", "buy", 0.1)
                mock_acc.assert_called()

    # --- Trading methods use _call_exchange ---

    def test_cancel_order_uses_exchange_lock(self):
        self.service.exchange.cancel_order = MagicMock()

        with patch.object(self.service, "_call_exchange") as mock_exc:
            self.service.cancel_order("BTC/USDT", "order-42")
            mock_exc.assert_called()

    def test_place_hard_sl_uses_exchange_lock(self):
        self.service.exchange.create_order = MagicMock(return_value={"id": "sl-1"})
        self.service.exchange.price_to_precision = MagicMock(return_value=49000.0)

        with patch.object(self.service, "_call_exchange") as mock_exc:
            mock_exc.return_value = {"id": "sl-1"}
            self.service.place_hard_sl("BTC/USDT", "buy", 0.1, 49000.0)
            mock_exc.assert_called()

    def test_close_position_uses_exchange_lock_for_cancel(self):
        self.service.exchange.cancel_all_orders = MagicMock()

        with patch.object(self.service, "_call_exchange") as mock_exc:
            with patch.object(self.service, "_call_exchange_account") as mock_acc:
                mock_acc.return_value = {"last": 50000.0}
                with patch(
                    "core.execution_service._execute_chase_limit_steps",
                    return_value={"exit_state": "FILLED"},
                ):
                    self.service.close_position("BTC/USDT", "buy", 0.1)
                    mock_exc.assert_called()

    def test_fetch_ticker_uses_exchange_lock(self):
        self.service.exchange.fetch_ticker = MagicMock(return_value={"last": 50000.0})

        with patch.object(self.service, "_call_exchange") as mock_exc:
            mock_exc.return_value = {"last": 50000.0}
            self.service.fetch_ticker("BTC/USDT")
            mock_exc.assert_called()

    def test_load_markets_uses_exchange_lock(self):
        self.service.exchange.load_markets = MagicMock(return_value={})

        with patch.object(self.service, "_call_exchange") as mock_exc:
            mock_exc.return_value = {}
            self.service.load_markets()
            mock_exc.assert_called()

    def test_create_reduce_only_market_order_uses_exchange_lock(self):
        self.service.exchange.create_order = MagicMock(return_value={"id": "ro-1"})

        with patch.object(self.service, "_call_exchange") as mock_exc:
            mock_exc.return_value = {"id": "ro-1"}
            self.service.create_reduce_only_market_order("BTC/USDT", "sell", 0.1)
            mock_exc.assert_called()

    def test_set_leverage_uses_exchange_lock(self):
        self.service.exchange.set_leverage = MagicMock(return_value={"leverage": 5})

        with patch.object(self.service, "_call_exchange") as mock_exc:
            mock_exc.return_value = {"leverage": 5}
            self.service.set_leverage(5, "BTC/USDT")
            mock_exc.assert_called()


if __name__ == "__main__":
    unittest.main()

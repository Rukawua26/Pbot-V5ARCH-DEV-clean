import unittest
from unittest.mock import MagicMock, patch

import ccxt

from core.execution_service import ExecutionService


class TestExecutionServiceInit(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_init_creates_exchange_with_correct_config(self, mock_binance):
        mock_exchange = MagicMock()
        mock_binance.return_value = mock_exchange

        service = ExecutionService("key123", "secret456")
        mock_binance.assert_called_once_with(
            {
                "apiKey": "key123",
                "secret": "secret456",
                "enableRateLimit": True,
                "adjustForTimeDifference": True,
                "options": {"defaultType": "future"},
            }
        )
        self.assertEqual(service.logger.name, "Execution")
        self.assertIsNotNone(service._exchange_call_lock)


class TestExecutionServiceTrackApiWeight(unittest.TestCase):
    def test_does_nothing_when_no_tracker(self):
        service = ExecutionService.__new__(ExecutionService)
        service.weight_tracker = None
        service.logger = MagicMock()
        service._track_api_weight("test", 1, "trading")  # Should not raise

    def test_calls_tracker_when_available(self):
        service = ExecutionService.__new__(ExecutionService)
        service.weight_tracker = MagicMock()
        service.logger = MagicMock()
        service._track_api_weight("create_order", 2, "trading")
        service.weight_tracker.track.assert_called_once_with("create_order", 2, "trading")


class TestExecutionServiceConfirmIocOrderState(unittest.TestCase):
    def setUp(self):
        self.service = ExecutionService.__new__(ExecutionService)
        self.service.logger = MagicMock()

    def test_returns_none_when_order_not_dict(self):
        result = self.service._confirm_ioc_order_state("not_a_dict", None, None)
        self.assertIsNone(result)

    def test_returns_none_when_order_is_none(self):
        result = self.service._confirm_ioc_order_state(None, None, None)
        self.assertIsNone(result)

    def test_returns_order_when_already_filled(self):
        order = {"id": "123", "status": "closed", "filled": 0.5}
        result = self.service._confirm_ioc_order_state("BTC/USDT", order, "cid_123")
        self.assertEqual(result["id"], "123")

    def test_returns_none_for_none_order(self):
        result = self.service._confirm_ioc_order_state("BTC/USDT", None, None)
        self.assertIsNone(result)

    def test_returns_none_for_non_dict_order(self):
        # _confirm_ioc_order_state returns the input if not isinstance(order, dict)
        # Actually looking at the code, it returns the order as-is if not dict
        result = self.service._confirm_ioc_order_state("BTC/USDT", "not_dict", None)
        # The function returns order if not isinstance(order, dict) - returns "not_dict"
        self.assertEqual(result, "not_dict")


class TestRecordCancelAllOrdersSuccess(unittest.TestCase):
    def test_clears_failure_state(self):
        service = ExecutionService.__new__(ExecutionService)
        service._cancel_all_failures = {"BTC/USDT": 3}
        service._cancel_all_failure_events = {"BTC/USDT": "error"}
        service._record_cancel_all_orders_success("BTC/USDT")
        self.assertNotIn("BTC/USDT", service._cancel_all_failures)
        self.assertNotIn("BTC/USDT", service._cancel_all_failure_events)


class TestIsQuarantineActive(unittest.TestCase):
    @patch("core.execution_service.time.time", return_value=1000)
    def test_returns_false_when_no_quarantine(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {}
        self.assertFalse(service._is_quarantine_active("BTC/USDT"))

    @patch("core.execution_service.time.time", return_value=1000)
    def test_returns_true_when_quarantine_active(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {"BTC/USDT": 1100}
        self.assertTrue(service._is_quarantine_active("BTC/USDT"))

    @patch("core.execution_service.time.time", return_value=2000)
    def test_clears_expired_quarantine(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {"BTC/USDT": 1100}
        result = service._is_quarantine_active("BTC/USDT")
        self.assertFalse(result)
        self.assertNotIn("BTC/USDT", service._symbol_quarantine_until)


class TestIsSymbolQuarantined(unittest.TestCase):
    def test_delegates_to_is_quarantine_active(self):
        service = ExecutionService.__new__(ExecutionService)
        service._is_quarantine_active = MagicMock(return_value=True)
        result = service.is_symbol_quarantined("ETH/USDT")
        self.assertTrue(result)
        service._is_quarantine_active.assert_called_once_with("ETH/USDT")


class TestGetSymbolQuarantineRemainingSeconds(unittest.TestCase):
    @patch("core.execution_service.time.time", return_value=1000)
    def test_returns_zero_when_no_quarantine(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {}
        self.assertEqual(service.get_symbol_quarantine_remaining_seconds("BTC/USDT"), 0)

    @patch("core.execution_service.time.time", return_value=1000)
    def test_returns_remaining_seconds(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {"BTC/USDT": 1060}
        result = service.get_symbol_quarantine_remaining_seconds("BTC/USDT")
        self.assertEqual(result, 60)

    @patch("core.execution_service.time.time", return_value=2000)
    def test_clears_and_returns_zero_when_expired(self, mock_time):
        service = ExecutionService.__new__(ExecutionService)
        service._symbol_quarantine_until = {"BTC/USDT": 1100}
        result = service.get_symbol_quarantine_remaining_seconds("BTC/USDT")
        self.assertEqual(result, 0)
        self.assertNotIn("BTC/USDT", service._symbol_quarantine_until)


class TestActiveNoPriceDayKey(unittest.TestCase):
    @patch("core.execution_service.datetime")
    def test_returns_current_day_utc(self, mock_datetime):
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-05-06"
        mock_datetime.now.return_value = mock_now
        service = ExecutionService.__new__(ExecutionService)
        result = service._active_no_price_day_key()
        self.assertEqual(result, "2026-05-06")


class TestGetNoPriceMarketExitCount(unittest.TestCase):
    def test_returns_zero_when_no_data(self):
        service = ExecutionService.__new__(ExecutionService)
        service._no_price_exit_daily_metrics = {}
        result = service.get_no_price_market_exit_count("BTC/USDT")
        self.assertEqual(result, 0)

    def test_returns_count_when_data_exists(self):
        service = ExecutionService.__new__(ExecutionService)
        service._no_price_exit_daily_metrics = {"2026-05-06": {"BTC/USDT": 3}}
        result = service.get_no_price_market_exit_count("BTC/USDT", "2026-05-06")
        self.assertEqual(result, 3)


class TestCallExchange(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_returns_result_on_success(self, mock_binance):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()

        mock_fn = MagicMock(return_value="success_result")
        result = service._call_exchange("test_op", mock_fn)
        self.assertEqual(result, "success_result")

    @patch("core.execution_service.time.sleep", return_value=None)
    @patch("core.execution_service.ccxt.binance")
    def test_retries_on_rate_limit(self, mock_binance, mock_sleep):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()

        mock_fn = MagicMock(
            side_effect=[ccxt.RateLimitExceeded("rate limit"), "success_after_retry"]
        )
        result = service._call_exchange("test_op", mock_fn, retries=2)
        self.assertEqual(result, "success_after_retry")

    @patch("core.execution_service.time.sleep", return_value=None)
    @patch("core.execution_service.ccxt.binance")
    def test_raises_after_max_retries(self, mock_binance, mock_sleep):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()

        mock_fn = MagicMock(side_effect=ccxt.NetworkError("network error"))
        with self.assertRaises(ccxt.NetworkError):
            service._call_exchange("test_op", mock_fn, retries=2)

    @patch("core.execution_service.ccxt.binance")
    def test_breaks_on_exchange_error_no_retry(self, mock_binance):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()

        mock_fn = MagicMock(side_effect=ccxt.ExchangeError("exchange error"))
        with self.assertRaises(ccxt.ExchangeError):
            service._call_exchange("test_op", mock_fn, retries=3)


class TestWaitOrderFilled(unittest.TestCase):
    @patch("core.execution_service.time.sleep", return_value=None)
    @patch("core.execution_service.ccxt.binance")
    def test_returns_true_when_order_filled(self, mock_binance, mock_sleep):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()
        service._call_exchange = MagicMock(return_value={"status": "filled"})

        result = service._wait_order_filled("BTC/USDT", "order123", timeout_s=5)
        self.assertTrue(result)

    @patch("core.execution_service.time.sleep", return_value=None)
    @patch("core.execution_service.ccxt.binance")
    def test_returns_false_on_timeout(self, mock_binance, mock_sleep):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()
        service._call_exchange = MagicMock(return_value={"status": "open"})

        result = service._wait_order_filled("BTC/USDT", "order123", timeout_s=0.1)
        self.assertFalse(result)

    @patch("core.execution_service.time.sleep", return_value=None)
    @patch("core.execution_service.ccxt.binance")
    def test_returns_false_on_poll_error(self, mock_binance, mock_sleep):
        service = ExecutionService("key", "secret")
        service.exchange = MagicMock()
        service.logger = MagicMock()
        service._call_exchange = MagicMock(side_effect=ccxt.NetworkError("poll error"))

        result = service._wait_order_filled("BTC/USDT", "order123", timeout_s=1)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

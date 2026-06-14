import threading
import unittest
from unittest.mock import patch

import ccxt

from core.execution_service import ExecutionService


class _ClientOrderLookupExchange:
    def __init__(self, lookup_error=None, lookup_order=None):
        self.timeout = 9000
        self.lookup_error = lookup_error
        self.lookup_order = lookup_order
        self.create_attempts = 0
        self.lookup_attempts = 0

    def market_id(self, _symbol):
        return "BTCUSDT"

    def fapiPrivateGetOrder(self, _params):
        self.lookup_attempts += 1
        if self.lookup_error:
            raise self.lookup_error
        return self.lookup_order or {}

    def price_to_precision(self, _symbol, price):
        return str(price)

    def create_order(self, *args, **kwargs):
        self.create_attempts += 1
        raise ccxt.RequestTimeout("create ack timeout")


class _AmbiguousIocExchange:
    def __init__(self):
        self.timeout = 9000
        self.fetch_attempts = 0

    def price_to_precision(self, _symbol, price):
        return str(price)

    def create_order(self, symbol, type, side, amount, price, params):
        return {
            "id": "entry-1",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0.0,
            "params": params,
        }

    def fetch_order(self, order_id, symbol):
        self.fetch_attempts += 1
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "closed",
            "filled": 0.1,
            "average": 100.05,
        }


class _FlakyCancelExchange:
    def __init__(self):
        self.timeout = 0
        self.cancel_attempts = 0

    def cancel_order(self, order_id, symbol):
        self.cancel_attempts += 1
        if self.cancel_attempts == 1:
            raise ccxt.NetworkError("temporary network issue")
        return {"id": order_id, "symbol": symbol, "status": "canceled"}


class _FlakyHardSlExchange:
    def __init__(self):
        self.timeout = 0
        self.create_attempts = 0

    def price_to_precision(self, _symbol, stop_price):
        return str(stop_price)

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_attempts += 1
        if self.create_attempts == 1:
            raise ccxt.RateLimitExceeded("rate limited")
        return {
            "id": "sl-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
        }


class _TimeoutProbeExchange:
    def __init__(self):
        self.timeout = 9000

    def cancel_order(self, order_id, symbol):
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class _BrokenMarketsExchange:
    @property
    def markets(self):
        raise RuntimeError("markets cache corrupted")


class _NoPriceExchange:
    def __init__(self):
        self.timeout = 9000
        self.market_exit_calls = 0

    def cancel_all_orders(self, _symbol):
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 0}

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.market_exit_calls += 1
        return {
            "id": "mkt-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
            "status": "closed",
        }


class _ChaseLimitNoFillExchange:
    def __init__(self):
        self.timeout = 9000
        self.created = []
        self.canceled = []

    def cancel_all_orders(self, _symbol):
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 100.0}

    def price_to_precision(self, _symbol, price):
        return str(round(float(price), 2))

    def create_order(self, symbol, order_type, side, amount, price, params):
        order = {
            "id": f"exit-{len(self.created) + 1}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "params": params,
        }
        self.created.append(order)
        return order

    def fetch_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "open"}

    def cancel_order(self, order_id, symbol):
        self.canceled.append(order_id)
        return {"id": order_id, "symbol": symbol, "status": "canceled"}


class _RetryCreateOrderExchange:
    def __init__(self):
        self.timeout = 9000
        self.params_seen = []
        self.create_attempts = 0

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_attempts += 1
        self.params_seen.append(dict(params or {}))
        if self.create_attempts == 1:
            raise ccxt.RequestTimeout("timeout after accept")
        return {"id": "ro-1", "symbol": symbol, "status": "closed", "params": params}


class _ConcurrentTimeoutExchange:
    def __init__(self):
        self.timeout = 9000
        self._lock = threading.Lock()
        self.seen_timeouts = []

    def cancel_order(self, order_id, symbol):
        with self._lock:
            self.seen_timeouts.append(self.timeout)
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class ExecutionServiceResilienceTest(unittest.TestCase):
    def test_has_markets_loaded_logs_lookup_failure(self):
        service = ExecutionService("k", "s")
        service.exchange = _BrokenMarketsExchange()

        with self.assertLogs("Execution", level="WARNING") as captured:
            loaded = service.has_markets_loaded()

        self.assertFalse(loaded)
        self.assertTrue(any("markets cargados" in message for message in captured.output))

    @patch("core.execution_service.time.sleep", return_value=None)
    def test_cancel_order_retries_on_network_error(self, _sleep_mock):
        service = ExecutionService("k", "s")
        service.exchange = _FlakyCancelExchange()
        service.set_weight_tracker(None)

        result = service.cancel_order("BTC/USDT", "order-123")

        self.assertEqual(result.get("status"), "canceled")
        self.assertEqual(service.exchange.cancel_attempts, 2)

    @patch("core.execution_service.time.sleep", return_value=None)
    def test_place_hard_sl_retries_on_rate_limit(self, _sleep_mock):
        service = ExecutionService("k", "s")
        service.exchange = _FlakyHardSlExchange()
        service.set_weight_tracker(None)

        result = service.place_hard_sl("BTC/USDT", side="BUY", amount=1.0, stop_price=99.5)

        self.assertIsNotNone(result)
        self.assertEqual(service.exchange.create_attempts, 2)
        self.assertEqual(service.last_hard_sl_error, "")

    def test_place_hard_sl_rejects_non_finite_stop_price(self):
        service = ExecutionService("k", "s")
        service.exchange = _FlakyHardSlExchange()
        service.set_weight_tracker(None)

        result = service.place_hard_sl("BTC/USDT", side="BUY", amount=1.0, stop_price=float("nan"))

        self.assertIsNone(result)
        self.assertIn("finite", service.last_hard_sl_error)
        self.assertEqual(service.exchange.create_attempts, 0)

    @patch("core.execution_service.time.sleep", return_value=None)
    def test_reduce_only_market_retry_reuses_client_order_id(self, _sleep_mock):
        service = ExecutionService("k", "s")
        service.exchange = _RetryCreateOrderExchange()
        service.set_weight_tracker(None)

        result = service.create_reduce_only_market_order("BTC/USDT", "sell", 0.1)

        self.assertEqual(result.get("status"), "closed")
        self.assertEqual(service.exchange.create_attempts, 2)
        first = service.exchange.params_seen[0].get("newClientOrderId")
        second = service.exchange.params_seen[1].get("newClientOrderId")
        self.assertTrue(first)
        self.assertEqual(first, second)

    @patch("core.execution_service.Config.PAPER_MODE", False)
    def test_real_get_balance_raises_instead_of_returning_cached_balance(self):
        service = ExecutionService("k", "s")
        service._last_valid_balance = 123.0

        with patch.object(service, "_call_exchange_account", side_effect=RuntimeError("auth down")):
            with self.assertRaisesRegex(RuntimeError, "REAL_BALANCE_UNAVAILABLE"):
                service.get_balance()

    def test_call_exchange_restores_timeout_after_operation(self):
        service = ExecutionService("k", "s")
        service.exchange = _TimeoutProbeExchange()
        service.set_weight_tracker(None)

        result = service.cancel_order("BTC/USDT", "order-timeout")

        self.assertEqual(result.get("status"), "canceled")
        self.assertEqual(result.get("timeout_seen"), 20000)
        self.assertEqual(service.exchange.timeout, 9000)

    @patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", True)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 1)
    @patch(
        "core.execution_service.time.monotonic",
        side_effect=[10.0, 10.2, 12.5, 12.5, 12.5, 12.5],
    )
    def test_no_price_escalates_to_market_exit_after_threshold(self, _mono_mock):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        first = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertIsNone(first)
        self.assertEqual(service.exchange.market_exit_calls, 1)
        escalated = second if second is not None else third
        self.assertIsNotNone(escalated)
        self.assertEqual(escalated.get("type"), "market")
        self.assertEqual(service.exchange.market_exit_calls, 1)

    @patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", False)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 1)
    @patch(
        "core.execution_service.time.monotonic",
        side_effect=[10.0, 10.2, 12.5, 12.5, 12.5, 12.5],
    )
    def test_no_price_does_not_market_exit_when_disabled(self, _mono_mock):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        first = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(service.exchange.market_exit_calls, 0)

    def test_dynamic_no_price_threshold_tunes_with_daily_exit_count(self):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        with (
            patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 180),
            patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 45),
        ):
            base = service._resolve_no_price_threshold("BTC/USDT")
            service._record_no_price_market_exit("BTC/USDT")
            tuned_once = service._resolve_no_price_threshold("BTC/USDT")
            for _ in range(10):
                service._record_no_price_market_exit("BTC/USDT")
            tuned_floor = service._resolve_no_price_threshold("BTC/USDT")

        self.assertEqual(base, 180)
        self.assertLess(tuned_once, base)
        self.assertGreaterEqual(tuned_floor, 45)

    @patch("core.execution_service.time.sleep", return_value=None)
    def test_close_position_returns_stuck_state_when_hard_floor_not_filled(self, _sleep):
        service = ExecutionService("k", "s")
        service.exchange = _ChaseLimitNoFillExchange()
        service.set_weight_tracker(None)

        result = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertEqual(result.get("exit_state"), "STUCK")
        self.assertEqual(result.get("id"), "exit-4")
        self.assertEqual(service.exchange.canceled, ["exit-1", "exit-2", "exit-3"])

    def test_cancel_all_degraded_activates_symbol_quarantine(self):
        service = ExecutionService("k", "s")
        service.exchange = _TimeoutProbeExchange()
        service.set_weight_tracker(None)

        with (
            patch("core.execution_service.Config.CANCEL_ALL_DEGRADED_WINDOW_SECONDS", 300),
            patch("core.execution_service.Config.CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS", 3),
            patch(
                "core.execution_service.Config.CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS",
                600,
            ),
            patch(
                "core.execution_service.time.time",
                return_value=30.0,
            ),
        ):
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e1"))
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e2"))
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e3"))
            self.assertTrue(service.is_symbol_quarantined("BTC/USDT"))
            remaining = service.get_symbol_quarantine_remaining_seconds("BTC/USDT")

        self.assertGreater(remaining, 0)

    def test_fetch_order_by_client_id_raises_on_lookup_transport_error(self):
        service = ExecutionService("k", "s")
        service.exchange = _ClientOrderLookupExchange(
            lookup_error=ccxt.NetworkError("temporary down")
        )

        with self.assertRaises(RuntimeError):
            service.fetch_order_by_client_id("BTC/USDT", "cid-1")

    def test_fetch_order_by_client_id_returns_none_for_not_found(self):
        service = ExecutionService("k", "s")
        service.exchange = _ClientOrderLookupExchange(
            lookup_error=ccxt.OrderNotFound("Order does not exist")
        )

        self.assertIsNone(service.fetch_order_by_client_id("BTC/USDT", "cid-1"))

    def test_create_precision_order_recovers_ack_by_client_order_id_after_timeout(self):
        service = ExecutionService("k", "s")
        service.exchange = _ClientOrderLookupExchange(
            lookup_order={
                "orderId": "ord-1",
                "status": "FILLED",
                "clientOrderId": "cid-1",
                "executedQty": "0.1",
                "avgPrice": "101.0",
            }
        )

        order = service.create_precision_order(
            "BTC/USDT", "BUY", 0.1, 100.0, client_order_id="cid-1"
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.get("id"), "ord-1")
        self.assertEqual(order.get("clientOrderId"), "cid-1")
        self.assertEqual(order.get("filled"), 0.1)
        self.assertEqual(order.get("average"), 101.0)
        self.assertEqual(service.exchange.create_attempts, 1)
        self.assertEqual(service.exchange.lookup_attempts, 1)

    @patch("core.execution_service.Config.ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS", 0.5)
    @patch("core.execution_service.time.sleep", return_value=None)
    def test_create_precision_order_confirms_ambiguous_ioc_fill(self, _sleep):
        service = ExecutionService("k", "s")
        service.exchange = _AmbiguousIocExchange()

        order = service.create_precision_order(
            "BTC/USDT", "BUY", 0.1, 100.0, client_order_id="cid-2"
        )

        self.assertEqual(order.get("status"), "closed")
        self.assertEqual(order.get("filled"), 0.1)
        self.assertEqual(service.exchange.fetch_attempts, 1)

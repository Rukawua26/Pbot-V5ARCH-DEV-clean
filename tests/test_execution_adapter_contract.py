import random
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.execution_adapters import ShadowExecutionAdapter, build_execution_gateway


class _FakeExecutionService:
    def __init__(self, _api_key, _api_secret):
        self.logger = MagicMock()
        self.exchange = SimpleNamespace(set_sandbox_mode=MagicMock(), options={})
        self.last_hard_sl_error = ""

    def fetch_ticker(self, _symbol):
        return {"last": 100.0}

    def has_markets_loaded(self) -> bool:
        return True

    def fetch_book_tickers(self):
        return {"BTC/USDT": {"bid": 100.0, "ask": 100.1}}

    def fetch_funding_rate(self, _symbol):
        return {"fundingRate": 0.0001}


class ExecutionAdapterContractTest(unittest.TestCase):
    def test_factory_builds_shadow_adapter(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=False,
            PAPER_MODE=True,
            EXECUTION_BACKEND="shadow_live",
            SHADOW_SIM_LATENCY_MIN_MS=0,
            SHADOW_SIM_LATENCY_MAX_MS=0,
            SHADOW_SIM_REJECT_RATE=0.0,
            SHADOW_SIM_PARTIAL_FILL_RATE=0.0,
            SHADOW_SIM_MIN_PARTIAL_RATIO=0.3,
        )

        execution = build_execution_gateway(config, _FakeExecutionService)

        self.assertIsInstance(execution, ShadowExecutionAdapter)

    def test_factory_rejects_shadow_adapter_in_real_mode(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=False,
            PAPER_MODE=False,
            EXECUTION_BACKEND="shadow_live",
        )

        with self.assertRaises(RuntimeError):
            build_execution_gateway(config, _FakeExecutionService)

    def test_factory_enables_testnet_on_base_execution(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=True,
            PAPER_MODE=False,
            EXECUTION_BACKEND="live",
        )

        execution = build_execution_gateway(config, _FakeExecutionService)

        execution.exchange.set_sandbox_mode.assert_called_once_with(True)

    def test_factory_uses_shadow_adapter_by_default_in_paper_mode(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=False,
            PAPER_MODE=True,
            EXECUTION_BACKEND="live",
            SHADOW_SIM_LATENCY_MIN_MS=0,
            SHADOW_SIM_LATENCY_MAX_MS=0,
            SHADOW_SIM_REJECT_RATE=0.0,
            SHADOW_SIM_PARTIAL_FILL_RATE=0.0,
            SHADOW_SIM_MIN_PARTIAL_RATIO=0.3,
        )

        execution = build_execution_gateway(config, _FakeExecutionService)

        self.assertIsInstance(execution, ShadowExecutionAdapter)

    def test_factory_rejects_unknown_backend(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=False,
            EXECUTION_BACKEND="shadow-lve",
        )

        with self.assertRaisesRegex(RuntimeError, "EXECUTION_BACKEND"):
            build_execution_gateway(config, _FakeExecutionService)

    def test_shadow_adapter_simulates_partial_fills(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=0.0,
            min_partial_ratio=0.5,
            random_source=random.Random(1),
            sleep_fn=lambda _s: None,
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-1"
        )
        self.assertIsInstance(order, dict)
        order = order or {}

        self.assertEqual(order.get("status"), "open")
        self.assertLess(float(order.get("filled") or 0.0), 2.0)
        self.assertEqual(order.get("clientOrderId"), "cid-1")

    def test_shadow_adapter_delegates_read_only_market_data(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(2),
            sleep_fn=lambda _s: None,
        )

        self.assertTrue(adapter.has_markets_loaded())
        self.assertIn("BTC/USDT", adapter.fetch_book_tickers())
        self.assertEqual(adapter.fetch_funding_rate("BTC/USDT")["fundingRate"], 0.0001)

    def test_shadow_adapter_latency_is_non_blocking_for_caller(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=400,
            max_latency_ms=400,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.6,
            random_source=random.Random(3),
        )

        started = time.perf_counter()
        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-2"
        )
        elapsed = time.perf_counter() - started

        self.assertIsInstance(order, dict)
        self.assertLess(elapsed, 0.1)

    def test_partial_fill_can_finalize_async(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=40,
            max_latency_ms=40,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.5,
            random_source=random.Random(4),
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-3"
        )
        self.assertIsInstance(order, dict)
        order = order or {}
        self.assertEqual(order.get("status"), "open")
        open_now = adapter.fetch_open_orders("BTC/USDT")
        self.assertTrue(any(o.get("id") == order.get("id") for o in open_now))

        time.sleep(0.08)
        open_later = adapter.fetch_open_orders("BTC/USDT")
        self.assertFalse(any(o.get("id") == order.get("id") for o in open_later))

    def test_shadow_open_orders_do_not_fall_back_to_live_orders(self):
        live = _FakeExecutionService("k", "s")
        live.fetch_open_orders = MagicMock(return_value=[{"id": "live-order"}])
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(5),
            sleep_fn=lambda _s: None,
        )

        self.assertEqual(adapter.fetch_open_orders("BTC/USDT"), [])
        live.fetch_open_orders.assert_not_called()

    def test_shadow_order_lookup_does_not_fall_back_to_live_orders(self):
        live = _FakeExecutionService("k", "s")
        live.fetch_order_by_client_id = MagicMock(return_value={"id": "live-order"})
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(6),
            sleep_fn=lambda _s: None,
        )

        self.assertIsNone(adapter.fetch_order_by_client_id("BTC/USDT", "missing"))
        live.fetch_order_by_client_id.assert_not_called()

    def test_shadow_fill_monotonic_markers_never_precede_ack(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=40,
            max_latency_ms=40,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.5,
            random_source=random.Random(11),
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-causal-1"
        )
        self.assertIsInstance(order, dict)
        order = order or {}
        ack_mono = float((order.get("info") or {}).get("shadow_ack_mono") or 0.0)
        self.assertGreater(ack_mono, 0.0)

        partial = adapter.fetch_order_by_client_id("BTC/USDT", "cid-causal-1") or {}
        partial_info = partial.get("info") or {}
        partial_mono = partial_info.get("shadow_partial_mono")
        self.assertIsNotNone(partial_mono)
        self.assertGreaterEqual(float(partial_mono), ack_mono)

        time.sleep(0.06)
        closed = adapter.fetch_order_by_client_id("BTC/USDT", "cid-causal-1") or {}
        closed_info = closed.get("info") or {}
        full_mono = closed_info.get("shadow_full_mono")
        self.assertIsNotNone(full_mono)
        self.assertGreaterEqual(float(full_mono), ack_mono)

    def test_partial_then_full_never_skips_intermediate_partial_state(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=20,
            max_latency_ms=20,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.5,
            random_source=random.Random(13),
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-causal-2"
        )
        self.assertIsInstance(order, dict)
        order = order or {}
        self.assertEqual(str(order.get("status") or "").lower(), "open")
        self.assertGreater(float(order.get("filled") or 0.0), 0.0)
        self.assertGreater(float(order.get("remaining") or 0.0), 0.0)

        time.sleep(0.06)
        second_poll = adapter.fetch_order_by_client_id("BTC/USDT", "cid-causal-2") or {}
        self.assertEqual(str(second_poll.get("status") or "").lower(), "closed")
        self.assertEqual(float(second_poll.get("remaining") or 0.0), 0.0)

    def test_shadow_adapter_sets_immediate_trigger_error_for_invalid_sl(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(7),
            sleep_fn=lambda _s: None,
        )

        sl_order = adapter.place_hard_sl("BTC/USDT", "BUY", 1.0, stop_price=101.0)

        self.assertIsNone(sl_order)
        self.assertIn("-2021", adapter.last_hard_sl_error)

    def test_shadow_reduce_only_market_order_preserves_exchange_side(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(8),
            sleep_fn=lambda _s: None,
        )

        order = adapter.create_reduce_only_market_order("BTC/USDT", "sell", 1.0)

        self.assertEqual(order.get("side"), "sell")


if __name__ == "__main__":
    unittest.main()

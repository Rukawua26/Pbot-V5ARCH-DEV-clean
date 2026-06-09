import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import Config
from core.signals.cvd_filter import apply_cvd_filter
from tools.ws_manager import BinanceWebSocket


class CVDWebSocketTests(unittest.TestCase):
    def test_cvd_disabled_url_only_depth_stream(self):
        ws = BinanceWebSocket(symbols=["BTC/USDT"], enable_cvd=False)

        self.assertIn("btcusdt@depth5@100ms", ws.url)
        self.assertNotIn("aggTrade", ws.url)

    def test_cvd_enabled_url_adds_aggtrade_stream(self):
        ws = BinanceWebSocket(symbols=["BTC/USDT", "ETH/USDT"], enable_cvd=True)

        self.assertIn("btcusdt@depth5@100ms", ws.url)
        self.assertIn("btcusdt@aggTrade", ws.url)
        self.assertIn("ethusdt@aggTrade", ws.url)

    def test_process_aggtrade_accumulates_buy_and_sell_aggressors(self):
        ws = BinanceWebSocket(symbols=["BTC/USDT"], enable_cvd=True, cvd_window_seconds=300)

        ws._process_data(
            {
                "stream": "btcusdt@aggTrade",
                "data": {"e": "aggTrade", "p": "100.0", "q": "2.0", "m": False},
            }
        )
        ws._process_data(
            {
                "stream": "btcusdt@aggTrade",
                "data": {"e": "aggTrade", "p": "100.0", "q": "1.0", "m": True},
            }
        )

        state = ws.get_cvd_state("BTC/USDT")

        self.assertEqual(state["buy_volume"], 200.0)
        self.assertEqual(state["sell_volume"], 100.0)
        self.assertEqual(state["cvd"], 100.0)
        self.assertAlmostEqual(state["imbalance"], 1.0 / 3.0, places=4)

    def test_depth_processing_still_updates_l2_state_with_cvd_enabled(self):
        ws = BinanceWebSocket(symbols=["BTC/USDT"], enable_cvd=True)

        ws._process_data(
            {
                "stream": "btcusdt@depth5@100ms",
                "data": {"b": [["99.0", "1"]], "a": [["101.0", "1"]]},
            }
        )

        state = ws.get_l2_state("BTC/USDT")
        self.assertEqual(state["bid"], 99.0)
        self.assertEqual(state["ask"], 101.0)

    def test_stop_closes_active_websocket(self):
        ws = BinanceWebSocket(symbols=["BTC/USDT"], enable_cvd=True)
        loop = SimpleNamespace(is_running=lambda: True)
        active_ws = SimpleNamespace(close=lambda: "close-coro")
        ws.is_running = True
        ws._loop = loop
        ws._ws = active_ws

        with patch("tools.ws_manager.asyncio.run_coroutine_threadsafe") as run_threadsafe:
            ws.stop()

        self.assertFalse(ws.is_running)
        self.assertTrue(ws._reconnect_flag)
        run_threadsafe.assert_called_once_with("close-coro", loop)


class CVDFilterTests(unittest.TestCase):
    def _bot_with_cvd(self, state):
        return SimpleNamespace(
            ws_manager=SimpleNamespace(get_cvd_state=lambda _symbol: state),
            logs=[],
            log=lambda msg: None,
        )

    def test_disabled_filter_passes_through(self):
        bot = self._bot_with_cvd({"total_volume": 5000, "imbalance": -0.5})
        ctx = {}

        with patch.object(Config, "CVD_FILTER_ENABLED", False):
            prob, passed, reason = apply_cvd_filter(bot, "BTC/USDT", "BUY", 80.0, ctx)

        self.assertEqual(prob, 80.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "CVD_DISABLED")
        self.assertEqual(ctx, {})

    def test_low_volume_passes_through(self):
        bot = self._bot_with_cvd({"total_volume": 100, "imbalance": 0.9})
        ctx = {}

        with (
            patch.object(Config, "CVD_FILTER_ENABLED", True),
            patch.object(Config, "CVD_MIN_QUOTE_VOLUME", 1000.0),
        ):
            prob, passed, reason = apply_cvd_filter(bot, "BTC/USDT", "BUY", 80.0, ctx)

        self.assertEqual(prob, 80.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "CVD_PASSTHROUGH_LOW_VOLUME")

    def test_buy_aligned_cvd_boosts_probability(self):
        bot = self._bot_with_cvd({"total_volume": 5000, "imbalance": 0.3})
        ctx = {}

        with (
            patch.object(Config, "CVD_FILTER_ENABLED", True),
            patch.object(Config, "CVD_MIN_QUOTE_VOLUME", 1000.0),
            patch.object(Config, "CVD_ALIGNED_WEIGHT", 1.05),
            patch("core.signals.cvd_filter.append_execution_event"),
        ):
            prob, passed, reason = apply_cvd_filter(bot, "BTC/USDT", "BUY", 80.0, ctx)

        self.assertEqual(prob, 84.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "CVD_ALIGNED_BUY")
        self.assertEqual(ctx["cvd_direction"], "BUY")
        self.assertEqual(ctx["cvd_weight"], 1.05)

    def test_buy_conflicting_cvd_penalizes_probability(self):
        bot = self._bot_with_cvd({"total_volume": 5000, "imbalance": -0.3})
        ctx = {}

        with (
            patch.object(Config, "CVD_FILTER_ENABLED", True),
            patch.object(Config, "CVD_MIN_QUOTE_VOLUME", 1000.0),
            patch.object(Config, "CVD_CONFLICT_WEIGHT", 0.85),
            patch("core.signals.cvd_filter.append_execution_event"),
        ):
            prob, passed, reason = apply_cvd_filter(bot, "BTC/USDT", "BUY", 80.0, ctx)

        self.assertEqual(prob, 68.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "CVD_CONFLICT_SELL_VS_BUY")
        self.assertEqual(ctx["cvd_direction"], "SELL")

    def test_neutral_cvd_keeps_probability(self):
        bot = self._bot_with_cvd({"total_volume": 5000, "imbalance": 0.01})
        ctx = {}

        with (
            patch.object(Config, "CVD_FILTER_ENABLED", True),
            patch.object(Config, "CVD_MIN_QUOTE_VOLUME", 1000.0),
            patch("core.signals.cvd_filter.append_execution_event"),
        ):
            prob, passed, reason = apply_cvd_filter(bot, "BTC/USDT", "BUY", 80.0, ctx)

        self.assertEqual(prob, 80.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "CVD_NEUTRAL")


if __name__ == "__main__":
    unittest.main()

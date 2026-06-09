import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from core.bot_cycles import (
    _get_cached_btc_indicator,
    _resolve_btc_market_indicators,
    run_market_context_cycle,
)
from core.bot_io_loops import _apply_ticker_stream_update


class _FakeSeries:
    def __init__(self, value):
        self._value = value

    @property
    def iloc(self):
        return self

    def __getitem__(self, index):
        return self._value


class _FakeEMAIndicator:
    call_count = 0

    def __init__(self, close_vals, window):
        type(self).call_count += 1
        self.close_vals = close_vals
        self.window = window

    def ema_indicator(self):
        return _FakeSeries(123.45)


class BotCyclesPerformanceTest(unittest.TestCase):
    def test_resolve_btc_indicators_prefers_precomputed_columns(self):
        bot = SimpleNamespace(log=MagicMock())
        df = pd.DataFrame(
            {
                "time": [1, 2],
                "close": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.0, 100.0],
                "EMA_200": [99.0, 100.0],
                "ADX_14": [19.0, 21.0],
            }
        )

        ema_200, adx_14 = _resolve_btc_market_indicators(bot, df)

        self.assertEqual(ema_200, 100.0)
        self.assertEqual(adx_14, 21.0)
        self.assertFalse(hasattr(bot, "_btc_indicator_fallback_cache"))

    def test_cached_btc_indicator_avoids_recomputing_same_candle(self):
        bot = SimpleNamespace(log=MagicMock())
        df = pd.DataFrame(
            {
                "time": list(range(250)),
                "close": [100.0 + i for i in range(250)],
            }
        )
        _FakeEMAIndicator.call_count = 0

        with patch("core.bot_cycles.ta_trend.EMAIndicator", _FakeEMAIndicator):
            first = _get_cached_btc_indicator(bot, df, "EMA_200")
            second = _get_cached_btc_indicator(bot, df, "EMA_200")

        self.assertEqual(first, 123.45)
        self.assertEqual(second, 123.45)
        self.assertEqual(_FakeEMAIndicator.call_count, 1)

    def test_ticker_stream_updates_btc_price_state(self):
        bot = SimpleNamespace(
            price_lock=threading.Lock(),
            live_prices={},
            live_prices_ts={},
            market_btc_price=0.0,
        )

        updated = _apply_ticker_stream_update(
            bot,
            [
                {"s": "BTCUSDT", "c": "65000.5"},
                {"s": "ETHUSDT", "c": "3200.0"},
            ],
        )

        self.assertEqual(updated, 2)
        self.assertEqual(bot.live_prices["BTCUSDT"], "65000.5")
        self.assertEqual(bot.market_btc_price, 65000.5)
        self.assertEqual(bot.market_btc_price_source, "WS_TICKER")
        self.assertGreater(bot.market_btc_price_ts, 0)

    def test_market_context_prefers_fresh_ws_btc_price(self):
        bot = SimpleNamespace(
            brain=SimpleNamespace(get_daily_real_pnl=lambda *_: (0.0, {})),
            daily_initial_balance=0.0,
            balance=1000.0,
            price_lock=threading.Lock(),
            live_prices={"BTCUSDT": "65000.5"},
            live_prices_ts={"BTCUSDT": 1.0},
            market_btc_price=0.0,
            market_btc_price_source="INIT",
            _get_cached_btc_data=lambda: None,
            execution=SimpleNamespace(fetch_ticker=MagicMock()),
            log=MagicMock(),
        )

        with patch("core.bot_cycles.monotonic_now", return_value=2.0):
            pnl_real_hoy = run_market_context_cycle(
                bot,
                {"BTC/USDT": {"last": 64000.0}},
            )

        self.assertEqual(pnl_real_hoy, 0.0)
        self.assertEqual(bot.market_btc_price, 65000.5)
        self.assertEqual(bot.market_btc_price_source, "WS_TICKER")
        bot.execution.fetch_ticker.assert_not_called()


if __name__ == "__main__":
    unittest.main()

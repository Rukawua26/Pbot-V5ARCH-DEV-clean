import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from core.market_breadth import calculate_market_breadth
from core.signals.context import _build_symbol_context
from core.signals.filters import _apply_entry_filters_and_adjust_prob


def _df(close, rsi, ema_200):
    return pd.DataFrame(
        {
            "close": [close],
            "rsi": [rsi],
            "ema_200": [ema_200],
        }
    )


class MarketBreadthTest(unittest.TestCase):
    def test_calculates_fear_when_market_dump_ratio_exceeds_threshold(self):
        results = {
            "A/USDT": {"data": (_df(90, 35, 100), None)},
            "B/USDT": {"data": (_df(80, 25, 100), None)},
            "C/USDT": {"data": (_df(70, 40, 100), None)},
            "D/USDT": {"data": (_df(120, 55, 100), None)},
        }

        breadth = calculate_market_breadth(results, fear_threshold=0.70)

        self.assertEqual(breadth.sentiment, "FEAR")
        self.assertEqual(breadth.dump_count, 3)
        self.assertEqual(breadth.total_count, 4)
        self.assertAlmostEqual(breadth.dump_ratio, 0.75)

    def test_calculates_greed_when_market_pump_ratio_exceeds_threshold(self):
        results = {
            "A/USDT": {"data": (_df(110, 55, 100), None)},
            "B/USDT": {"data": (_df(120, 72, 100), None)},
            "C/USDT": {"data": (_df(130, 60, 100), None)},
            "D/USDT": {"data": (_df(80, 45, 100), None)},
        }

        breadth = calculate_market_breadth(results, greed_threshold=0.70)

        self.assertEqual(breadth.sentiment, "GREED")
        self.assertEqual(breadth.pump_count, 3)
        self.assertEqual(breadth.total_count, 4)

    def test_returns_neutral_without_enough_valid_rows(self):
        breadth = calculate_market_breadth({"A/USDT": {"data": (pd.DataFrame(), None)}})

        self.assertEqual(breadth.sentiment, "NEUTRAL")
        self.assertEqual(breadth.total_count, 0)

    @patch("core.signals.context.Strategy.detect_order_block", return_value="⚪")
    @patch(
        "core.signals.context.Strategy.compute_runtime_snapshot",
        return_value={"rows": 200, "ema": 99.0, "adx": 30.0, "rsi": 55.0, "atr": 1.0},
    )
    def test_symbol_context_propagates_market_breadth_and_hmm(self, _snapshot, _ob):
        hmm = {"is_ready": True, "state": "RANGE", "ts": "2026-06-11T00:00:00+00:00"}
        bot = SimpleNamespace(
            market_breadth={"sentiment": "FEAR", "dump_ratio": 0.75, "pump_ratio": 0.10},
            hmm_markov_snapshot=hmm,
            _snapshot_tickers={},
            _get_cached_funding_rate=MagicMock(return_value=0.0),
            _raw_snapshot_log_count=99,
            log=MagicMock(),
        )
        df = pd.DataFrame({"close": [100.0], "volume": [10.0], "volume_ma": [5.0]})

        _decision, ctx, _ob_status, _vol_rel = _build_symbol_context(
            bot, "BTC/USDT", "BTC/USDT", df, 100.0, {"mode": "X"}, "BUY"
        )

        self.assertEqual(ctx["market_breadth_sentiment"], "FEAR")
        self.assertEqual(ctx["market_breadth_dump_ratio"], 0.75)
        self.assertIs(ctx["hmm_data"], hmm)

    @patch("core.signals.filters.Config.BREAKOUT_WATCH_ENABLED", False)
    @patch("core.signals.filters.Strategy.check_entry_filters")
    def test_entry_filter_vetoes_buy_when_market_breadth_is_fear(self, mocked_filters):
        mocked_filters.return_value = (True, "Filter Pass", "RANGO", {})
        bot = SimpleNamespace(
            db_lock=RLock(),
            brain=SimpleNamespace(
                get_genetic_params=MagicMock(return_value={}),
                get_stats_by_trend=MagicMock(return_value={}),
            ),
            log=MagicMock(),
            _get_shock_distance_pct=MagicMock(return_value=(None, None)),
            _get_market_regime=MagicMock(return_value="RANGE"),
            _calculate_quant_consensus=MagicMock(side_effect=lambda prob, _ctx: (prob, "OK")),
            bootstrap_heuristic_mode=False,
        )
        ctx = {
            "rsi": 55,
            "adx": 25,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "market_breadth_sentiment": "FEAR",
            "market_breadth_dump_ratio": 0.75,
        }

        _prob, passed, reason, updated_ctx = _apply_entry_filters_and_adjust_prob(
            bot,
            "BTC/USDT",
            "BTC/USDT",
            _df(100, 55, 99),
            "BUY",
            80.0,
            ctx,
            1.2,
        )

        self.assertFalse(passed)
        self.assertIn("MARKET_BREADTH_FEAR", reason)
        self.assertEqual(updated_ctx["market_breadth_sentiment"], "FEAR")


if __name__ == "__main__":
    unittest.main()

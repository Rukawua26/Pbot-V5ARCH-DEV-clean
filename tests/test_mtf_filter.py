import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from config import Config
from core.signals import filters
from core.signals.mtf.analyzer import analyze_mtf_alignment
from core.signals.mtf.filter import apply_mtf_filter

_UPTREND = list(range(100, 120))
_DOWNTREND = list(range(119, 99, -1))
_FLAT = [100] * 20


def _make_df(closes):
    rows = []
    for idx, close in enumerate(closes):
        rows.append(
            {
                "time": idx,
                "open": float(close),
                "high": float(close) + 1.0,
                "low": float(close) - 1.0,
                "close": float(close),
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class MTFAnalyzerTests(unittest.TestCase):
    def test_buy_aligned_timeframes_returns_confirmation_weight(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_UPTREND)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertGreaterEqual(weight, 1.0)
        self.assertIn("ALIGNED", reason)

    def test_buy_vetoes_when_15m_opposes_signal(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_DOWNTREND)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertEqual(weight, 0.0)
        self.assertIn("VETO", reason)
        self.assertIn("15M", reason)

    def test_sell_vetoes_when_15m_opposes_signal(self):
        df_1h = _make_df(_DOWNTREND)
        df_15m = _make_df(_UPTREND)
        df_5m = _make_df(_DOWNTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "SELL")

        self.assertEqual(weight, 0.0)
        self.assertIn("VETO", reason)
        self.assertIn("15M", reason)

    def test_missing_intraday_data_passes_through(self):
        df_1h = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, None, None, "BUY")

        self.assertEqual(weight, 1.0)
        self.assertEqual(reason, "MTF_PASSTHROUGH_NO_INTRADAY_DATA")

    def test_5m_opposition_only_reduces_confidence(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_UPTREND)
        df_5m = _make_df(_DOWNTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertGreater(weight, 0.0)
        self.assertLess(weight, 1.0)
        self.assertIn("5M", reason)

    def test_15m_neutral_5m_aligns_returns_boost(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_FLAT)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertGreater(weight, 0.85)
        self.assertLessEqual(weight, 0.95)
        self.assertIn("5M_ALIGNED", reason)

    def test_15m_neutral_5m_opposes_returns_penalty(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_FLAT)
        df_5m = _make_df(_DOWNTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertLess(weight, 0.75)
        self.assertEqual(weight, 0.60)
        self.assertIn("5M_CONFLICT", reason)

    def test_15m_neutral_no_5m_returns_085(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_FLAT)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, None, "BUY")

        self.assertEqual(weight, 0.85)
        self.assertEqual(reason, "MTF_PARTIAL_15M_NEUTRAL")

    def test_15m_neutral_5m_neutral_returns_085(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_FLAT)
        df_5m = _make_df([100] * 20)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY")

        self.assertEqual(weight, 0.85)
        self.assertEqual(reason, "MTF_PARTIAL_15M_NEUTRAL")

    def test_15m_neutral_vetoes_sell_when_5m_opposes(self):
        df_1h = _make_df(_DOWNTREND)
        df_15m = _make_df(_FLAT)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "SELL")

        self.assertEqual(weight, 0.60)
        self.assertIn("5M_CONFLICT", reason)

    def test_neutral_signal_passes_through(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_DOWNTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, None, "NEUTRAL")

        self.assertEqual(weight, 1.0)
        self.assertEqual(reason, "MTF_PASSTHROUGH_UNSUPPORTED_SIGNAL")

    # --- Regime-aware MTF: pullback handling ---

    def test_bull_trend_buy_15m_opposes_returns_pullback_not_veto(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_DOWNTREND)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY", regime="BULL_TREND")

        self.assertGreater(weight, 0.0)
        self.assertIn("PULLBACK", reason)
        self.assertAlmostEqual(weight, 0.75)

    def test_bear_trend_sell_15m_opposes_returns_pullback_not_veto(self):
        df_1h = _make_df(_DOWNTREND)
        df_15m = _make_df(_UPTREND)
        df_5m = _make_df(_DOWNTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "SELL", regime="BEAR_TREND")

        self.assertGreater(weight, 0.0)
        self.assertIn("PULLBACK", reason)
        self.assertAlmostEqual(weight, 0.75)

    def test_range_regime_still_vetoes_15m_conflict(self):
        df_1h = _make_df(_UPTREND)
        df_15m = _make_df(_DOWNTREND)
        df_5m = _make_df(_UPTREND)

        weight, reason = analyze_mtf_alignment(df_1h, df_15m, df_5m, "BUY", regime="RANGE")

        self.assertEqual(weight, 0.0)
        self.assertIn("VETO", reason)


class _FakeDataService:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def fetch_and_update_data(self, symbol, timeframe, fast_mode=False):
        self.calls.append((symbol, timeframe, fast_mode))
        return self.frames.get(timeframe)


class _FakeBot:
    def __init__(self, frames):
        self.data_service = _FakeDataService(frames)
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class MTFFilterTests(unittest.TestCase):
    def test_filter_disabled_does_not_fetch_or_adjust(self):
        bot = _FakeBot({"15m": _make_df(_DOWNTREND)})
        ctx = {}

        with patch.object(Config, "MTF_FILTER_ENABLED", False):
            prob, passed, reason = apply_mtf_filter(
                bot, "BTC/USDT", "BUY", 70.0, ctx, _make_df(_UPTREND)
            )

        self.assertEqual(prob, 70.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "MTF_DISABLED")
        self.assertEqual(bot.data_service.calls, [])

    def test_enabled_filter_vetoes_15m_conflict(self):
        bot = _FakeBot(
            {
                "15m": _make_df(_DOWNTREND),
                "5m": _make_df(_UPTREND),
            }
        )
        ctx = {}

        with (
            patch.object(Config, "MTF_FILTER_ENABLED", True),
            patch("core.signals.mtf.filter.append_execution_event"),
        ):
            prob, passed, reason = apply_mtf_filter(
                bot, "BTC/USDT", "BUY", 70.0, ctx, _make_df(_UPTREND)
            )

        self.assertEqual(prob, 70.0)
        self.assertFalse(passed)
        self.assertIn("MTF_VETO", reason)
        self.assertEqual(ctx["mtf_weight"], 0.0)
        self.assertIn("15M", ctx["mtf_reason"])

    def test_enabled_filter_adjusts_probability_on_5m_conflict(self):
        bot = _FakeBot(
            {
                "15m": _make_df(_UPTREND),
                "5m": _make_df(_DOWNTREND),
            }
        )
        ctx = {}

        with (
            patch.object(Config, "MTF_FILTER_ENABLED", True),
            patch("core.signals.mtf.filter.append_execution_event"),
        ):
            prob, passed, reason = apply_mtf_filter(
                bot, "BTC/USDT", "BUY", 80.0, ctx, _make_df(_UPTREND)
            )

        self.assertEqual(prob, 60.0)
        self.assertTrue(passed)
        self.assertIn("5M", reason)
        self.assertEqual(ctx["mtf_weight"], 0.75)

    def test_entry_pipeline_mtf_disabled_does_not_fetch_intraday_data(self):
        class RaisingDataService:
            def fetch_and_update_data(self, *_args, **_kwargs):
                raise AssertionError("MTF data fetch should not run when disabled")

        bot = SimpleNamespace(
            db_lock=RLock(),
            brain=SimpleNamespace(
                get_genetic_params=lambda *_: {},
                get_stats_by_trend=lambda: {},
            ),
            data_service=RaisingDataService(),
            log=lambda *_: None,
            _get_shock_distance_pct=lambda *_: (None, None),
            _get_market_regime=lambda: "CALM",
            _calculate_quant_consensus=lambda prob, _ctx: (prob, "OK"),
            bootstrap_heuristic_mode=False,
        )
        ctx = {
            "rsi": 55.0,
            "adx": 25.0,
            "atr_pct": 0.01,
            "close": 100.0,
            "atr": 1.0,
            "trend": "RANGO",
            "tier": "IRON",
        }

        with (
            patch.object(filters.Config, "MTF_FILTER_ENABLED", False),
            patch.object(filters.Config, "OI_FILTER_ENABLED", False),
            patch.object(filters.Config, "BREAKOUT_WATCH_ENABLED", False),
            patch.object(
                filters.Strategy,
                "check_entry_filters",
                return_value=(True, "OK", "CALM", {"DAY_WEIGHT": 1.0, "HOUR_WEIGHT": 1.0}),
            ),
            patch.object(filters, "append_execution_event"),
        ):
            prob, passed, reason, updated_ctx = filters._apply_entry_filters_and_adjust_prob(
                bot,
                "BTC/USDT",
                "BTC/USDT",
                _make_df(_UPTREND),
                "BUY",
                80.0,
                ctx,
                1.0,
            )

        self.assertEqual(prob, 80.0)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")
        self.assertNotIn("mtf_weight", updated_ctx)


if __name__ == "__main__":
    unittest.main()

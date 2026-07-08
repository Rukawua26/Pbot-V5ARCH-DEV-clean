import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from config import Config
from core.signals.analyze import _get_fast_coherence_veto_reason
from core.signals.filters import (
    _apply_ema_alignment_filter,
    _evaluate_bootstrap_heuristic,
    _get_markov_snapshot_mode,
    _is_shadow_learning_runtime,
    _normalize_filter_reason,
    _resolve_btc_regime_adjustment,
    _signal_markov_probability,
    _snapshot_age_seconds,
)


class TestNormalizeFilterReason(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(_normalize_filter_reason(None), "")

    def test_strips_veto_prefix(self):
        self.assertEqual(_normalize_filter_reason("VETO: bad volume"), "bad volume")

    def test_strips_veto_lowercase(self):
        self.assertEqual(_normalize_filter_reason("veto: something"), "something")

    def test_no_veto_returns_original(self):
        self.assertEqual(_normalize_filter_reason("some reason"), "some reason")

    def test_empty_returns_empty(self):
        self.assertEqual(_normalize_filter_reason(""), "")


class TestSnapshotAgeSeconds(unittest.TestCase):
    def test_no_snapshot_returns_inf(self):
        self.assertEqual(_snapshot_age_seconds(None), float("inf"))

    def test_no_ts_in_dict_returns_inf(self):
        self.assertEqual(_snapshot_age_seconds({}), float("inf"))

    def test_exception_returns_inf(self):
        self.assertEqual(_snapshot_age_seconds({"ts": object()}), float("inf"))

    @patch("datetime.datetime")
    def test_recent_ts_returns_small_age(self, mock_dt):
        mock_dt.now.return_value = datetime(2025, 1, 15, 12, 0, 5, tzinfo=UTC)
        mock_dt.fromisoformat.return_value = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        age = _snapshot_age_seconds({"ts": "2025-01-15T12:00:00Z"})
        self.assertAlmostEqual(age, 5.0)


class TestGetMarkovSnapshotMode(unittest.TestCase):
    def test_missing_on_non_dict(self):
        self.assertEqual(_get_markov_snapshot_mode(None), "missing")

    def test_missing_when_not_ready(self):
        self.assertEqual(_get_markov_snapshot_mode({"is_ready": False}), "missing")

    @patch("core.signals.filters._snapshot_age_seconds", return_value=100)
    def test_fresh_when_within_max_age(self, _):
        self.assertEqual(_get_markov_snapshot_mode({"is_ready": True, "ts": "x"}), "fresh")

    @patch("core.signals.filters._snapshot_age_seconds", return_value=5 * 60 * 60)
    def test_stale_when_between_max_and_stale(self, _):
        self.assertEqual(
            _get_markov_snapshot_mode({"is_ready": True, "ts": "x"}), "stale_penalty_only"
        )

    @patch("core.signals.filters._snapshot_age_seconds", return_value=7 * 60 * 60)
    def test_expired_when_past_stale(self, _):
        self.assertEqual(_get_markov_snapshot_mode({"is_ready": True, "ts": "x"}), "expired")


class TestSignalMarkovProbability(unittest.TestCase):
    def test_buy_uses_bullish_breakout_prob(self):
        snap = {"bullish_breakout_prob": 75.0}
        self.assertEqual(_signal_markov_probability(snap, "BUY"), 75.0)

    def test_buy_falls_back_to_breakout_prob(self):
        snap = {"breakout_prob": 60.0}
        self.assertEqual(_signal_markov_probability(snap, "BUY"), 60.0)

    def test_sell_uses_bearish_reversal_prob(self):
        snap = {"bearish_reversal_prob": 30.0}
        self.assertEqual(_signal_markov_probability(snap, "SELL"), 30.0)

    def test_sell_falls_back_to_breakout_prob(self):
        snap = {"breakout_prob": 55.0}
        self.assertEqual(_signal_markov_probability(snap, "SELL"), 55.0)

    def test_unknown_signal_uses_breakout_prob(self):
        snap = {"breakout_prob": 50.0}
        self.assertEqual(_signal_markov_probability(snap, "HOLD"), 50.0)


class TestEvaluateBootstrapHeuristic(unittest.TestCase):
    def test_invalid_signal_returns_empty(self):
        r = _evaluate_bootstrap_heuristic("HOLD", {})
        self.assertEqual(r["heuristic_hits"], [])
        self.assertEqual(r["heuristic_confidence"], 0.0)

    def test_not_a_dict_returns_empty(self):
        r = _evaluate_bootstrap_heuristic("BUY", None)
        self.assertEqual(r["heuristic_confidence"], 0.0)

    def test_buy_all_hits(self):
        ctx = {
            "rsi": 60.0,
            "adx": 25.0,
            "vol_rel": 1.5,
            "atr_pct": 0.03,
            "close": 110.0,
            "ema": 100.0,
        }
        r = _evaluate_bootstrap_heuristic("BUY", ctx)
        self.assertIn("EMA_ALIGN", r["heuristic_hits"])
        self.assertIn("ADX_OK", r["heuristic_hits"])
        self.assertIn("RSI_OK", r["heuristic_hits"])
        self.assertIn("VOL_OK", r["heuristic_hits"])
        self.assertIn("ATR_OK", r["heuristic_hits"])
        self.assertTrue(r["bootstrap_ready_shadow"])
        self.assertTrue(r["bootstrap_ready_real"])

    def test_buy_no_hits(self):
        ctx = {"rsi": 30.0, "adx": 5.0, "vol_rel": 0.5, "atr_pct": 0.1, "close": 90.0, "ema": 100.0}
        r = _evaluate_bootstrap_heuristic("BUY", ctx)
        self.assertEqual(r["heuristic_hits"], [])
        self.assertFalse(r["bootstrap_ready_shadow"])
        self.assertFalse(r["bootstrap_ready_real"])

    def test_sell_ema_align(self):
        ctx = {
            "rsi": 40.0,
            "adx": 20.0,
            "vol_rel": 1.2,
            "atr_pct": 0.02,
            "close": 90.0,
            "ema": 100.0,
        }
        r = _evaluate_bootstrap_heuristic("SELL", ctx)
        self.assertIn("EMA_ALIGN", r["heuristic_hits"])


class TestApplyEmaAlignmentFilter(unittest.TestCase):
    def test_disabled_filter_allows_signal(self):
        with patch.object(Config, "EMA_ALIGNMENT_FILTER_ENABLED", False):
            ok, reason = _apply_ema_alignment_filter("BUY", {})
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_cross_buy_requires_fast_ema_and_close_above_ema50(self):
        ctx = {"close": 105.0, "ema_9": 103.0, "ema_21": 101.0, "ema": 100.0}
        with (
            patch.object(Config, "EMA_ALIGNMENT_FILTER_ENABLED", True),
            patch.object(Config, "EMA_ALIGNMENT_MODE", "cross"),
            patch.object(Config, "EMA_SLOPE_FILTER_ENABLED", False),
        ):
            ok, reason = _apply_ema_alignment_filter("BUY", ctx)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_cross_sell_rejects_when_fast_ema_not_bearish(self):
        ctx = {"close": 95.0, "ema_9": 101.0, "ema_21": 100.0, "ema": 100.0}
        with (
            patch.object(Config, "EMA_ALIGNMENT_FILTER_ENABLED", True),
            patch.object(Config, "EMA_ALIGNMENT_MODE", "cross"),
            patch.object(Config, "EMA_SLOPE_FILTER_ENABLED", False),
        ):
            ok, reason = _apply_ema_alignment_filter("SELL", ctx)
        self.assertFalse(ok)
        self.assertIn("EMA_ALIGN_cross", reason)

    def test_slope_filter_rejects_buy_when_ema50_slope_is_negative(self):
        ctx = {
            "close": 105.0,
            "ema_9": 103.0,
            "ema_21": 101.0,
            "ema": 100.0,
            "ema50_slope": -0.001,
        }
        with (
            patch.object(Config, "EMA_ALIGNMENT_FILTER_ENABLED", True),
            patch.object(Config, "EMA_ALIGNMENT_MODE", "cross"),
            patch.object(Config, "EMA_SLOPE_FILTER_ENABLED", True),
        ):
            ok, reason = _apply_ema_alignment_filter("BUY", ctx)
        self.assertFalse(ok)
        self.assertIn("EMA_ALIGN_cross", reason)


class TestIsShadowLearningRuntime(unittest.TestCase):
    def test_paper_shadow_mode_returns_true(self):
        with (
            patch.object(Config, "PAPER_MODE", True),
            patch.object(Config, "EXECUTION_BACKEND", "shadow_live"),
        ):
            bot = SimpleNamespace(execution_mode="shadow")
            self.assertTrue(_is_shadow_learning_runtime(bot))

    def test_real_mode_returns_false(self):
        with patch.object(Config, "PAPER_MODE", False):
            bot = SimpleNamespace(execution_mode="live")
            self.assertFalse(_is_shadow_learning_runtime(bot))

    def test_shadow_live_backend_returns_true(self):
        with (
            patch.object(Config, "PAPER_MODE", True),
            patch.object(Config, "EXECUTION_BACKEND", "shadow_live"),
        ):
            bot = SimpleNamespace(execution_mode="live")
            self.assertTrue(_is_shadow_learning_runtime(bot))


class TestResolveBtcRegimeAdjustment(unittest.TestCase):
    def test_bull_buy(self):
        w, r, v = _resolve_btc_regime_adjustment("BUY", "BULL_TREND")
        self.assertEqual(w, 1.15)
        self.assertEqual(r, "BULL_ALIGNED")
        self.assertFalse(v)

    def test_bull_sell(self):
        w, r, v = _resolve_btc_regime_adjustment("SELL", "BULL_TREND")
        self.assertEqual(w, 0.85)
        self.assertEqual(r, "BULL_COUNTER")

    def test_bear_sell(self):
        w, r, v = _resolve_btc_regime_adjustment("SELL", "BEAR_TREND")
        self.assertEqual(w, 1.15)
        self.assertEqual(r, "BEAR_ALIGNED")

    def test_bear_buy(self):
        with patch.object(Config, "BEAR_COUNTER_WEIGHT", 0.70):
            w, r, v = _resolve_btc_regime_adjustment("BUY", "BEAR_TREND")
            self.assertAlmostEqual(w, 0.85 * 0.70)
            self.assertEqual(r, "BEAR_COUNTER")

    def test_range_penalty(self):
        with (
            patch.object(Config, "HMM_RANGE_PENALTY", 0.5),
            patch.object(Config, "HMM_RANGE_VETO", False),
        ):
            w, r, v = _resolve_btc_regime_adjustment("BUY", "RANGE")
            self.assertEqual(w, 0.5)
            self.assertEqual(r, "RANGE_PENALTY")
            self.assertFalse(v)

    def test_range_veto(self):
        with (
            patch.object(Config, "HMM_RANGE_PENALTY", 0.5),
            patch.object(Config, "HMM_RANGE_VETO", True),
        ):
            w, r, v = _resolve_btc_regime_adjustment("BUY", "RANGE")
            self.assertEqual(w, 0.0)
            self.assertEqual(r, "RANGE_VETO")
            self.assertTrue(v)

    def test_unknown_regime(self):
        w, r, v = _resolve_btc_regime_adjustment("BUY", "UNKNOWN")
        self.assertEqual(r, "RANGE_NEUTRAL")
        self.assertFalse(v)


class TestFastCoherenceVeto(unittest.TestCase):
    def test_uses_real_strategy_signal_when_provided(self):
        import pandas as pd

        bot = SimpleNamespace(current_sentiment=("ALCISTA",))
        df = pd.DataFrame({"close": [90.0], "ema": [100.0]})

        with patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", True):
            self.assertIsNone(_get_fast_coherence_veto_reason(bot, df, signal="BUY"))
            reason = _get_fast_coherence_veto_reason(bot, df, signal="SELL")

        self.assertIn("SELL bloqueado", reason)


if __name__ == "__main__":
    unittest.main()

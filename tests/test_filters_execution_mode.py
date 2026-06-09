import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config import Config
from core.signals.filters import _plan_execution_mode, _resolve_audit_verdict_and_stats


def _make_bot(**attrs):
    defaults = dict(
        _get_market_regime=MagicMock(return_value="RANGE"),
        current_sentiment=["🟢 TENDENCIA ALCISTA", 1.0],
        breakout_agent=SimpleNamespace(add_to_watchlist=MagicMock(return_value=True)),
        breakout_overrides_today=0,
        bootstrap_heuristic_mode=False,
        log=MagicMock(),
        get_audit_verdict=MagicMock(return_value="STRONG_BUY"),
        current_target=0.05,
    )
    defaults.update(attrs)
    bot = SimpleNamespace(**defaults)
    return bot


def _ctx(**kw):
    ctx = {"atr": 1.5, "shock_level": 0.02, "trend": "RANGO", "shock_dist_pct": 0.5}
    ctx.update(kw)
    return ctx


class TestPlanExecutionMode(unittest.TestCase):
    def test_normal_shadow_execution(self):
        bot = _make_bot()
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 60.0, "OK", True, "", _ctx()
            )
        self.assertTrue(ok)
        self.assertTrue(shadow)

    def test_normal_real_execution(self):
        bot = _make_bot()
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 80.0, "OK", True, "", _ctx()
            )
        self.assertTrue(ok)
        self.assertFalse(shadow)

    def test_prob_below_shadow_min_no_execution(self):
        bot = _make_bot()
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 30.0, "OK", True, "", _ctx()
            )
        self.assertFalse(ok)

    def test_directional_coherence_bull_sell_blocked(self):
        bot = _make_bot(
            current_sentiment=["🟢 TENDENCIA ALCISTA", 1.0],
            breakout_agent=SimpleNamespace(add_to_watchlist=MagicMock(return_value=True)),
        )
        with (
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "BREAKOUT_WATCH_ENABLED", True),
            patch.object(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", True),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "SELL", 60.0, "OK", True, "", _ctx(shock_level=0.02)
            )
        self.assertFalse(ok)
        self.assertFalse(fp)
        self.assertIn("COHERENCIA", fr)

    def test_directional_coherence_bear_buy_blocked(self):
        bot = _make_bot(
            current_sentiment=["🔴 TENDENCIA BAJISTA", -1.0],
            breakout_agent=SimpleNamespace(add_to_watchlist=MagicMock(return_value=True)),
        )
        with (
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "BREAKOUT_WATCH_ENABLED", True),
            patch.object(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", True),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 60.0, "OK", True, "", _ctx(shock_level=0.02)
            )
        self.assertFalse(ok)
        self.assertFalse(fp)
        self.assertIn("COHERENCIA", fr)

    def test_bootstrap_heuristic_mode_real(self):
        bot = _make_bot(bootstrap_heuristic_mode=True)
        with patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot,
                "BTC/USDT",
                "BUY",
                60.0,
                "OK",
                True,
                "",
                _ctx(
                    heuristic_hits=[1, 2, 3, 4, 5],
                    bootstrap_ready_real=True,
                    bootstrap_ready_shadow=False,
                ),
            )
        self.assertTrue(ok)
        self.assertFalse(shadow)

    def test_bootstrap_heuristic_mode_shadow(self):
        bot = _make_bot(bootstrap_heuristic_mode=True)
        with patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot,
                "BTC/USDT",
                "BUY",
                60.0,
                "OK",
                True,
                "",
                _ctx(
                    heuristic_hits=[1, 2, 3],
                    bootstrap_ready_real=False,
                    bootstrap_ready_shadow=True,
                ),
            )
        self.assertTrue(ok)
        self.assertTrue(shadow)

    def test_bootstrap_no_fire(self):
        bot = _make_bot(bootstrap_heuristic_mode=True)
        with patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot,
                "BTC/USDT",
                "BUY",
                60.0,
                "OK",
                True,
                "",
                _ctx(heuristic_hits=[1], bootstrap_ready_real=False, bootstrap_ready_shadow=False),
            )
        self.assertFalse(ok)

    def test_bear_trend_confidence_boost(self):
        bot = _make_bot(_get_market_regime=MagicMock(return_value="BEAR_TREND"))
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BEAR_TREND_CONFIDENCE_BOOST", 10.0),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 80.0, "SCOUT", True, "", _ctx()
            )
        self.assertTrue(ok)

    def test_degradation_to_shadow(self):
        bot = _make_bot()
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", False),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "BUY", 55.0, "SCOUT", True, "", _ctx()
            )
        self.assertTrue(ok)
        self.assertTrue(shadow)

    def test_breakout_shadow_override(self):
        bot = _make_bot()
        with (
            patch.object(Config, "REAL_CONFIDENCE_MIN", 0.75),
            patch.object(Config, "SHADOW_PROB_MIN", 0.50),
            patch.object(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", True),
            patch.object(Config, "BREAKOUT_MIN_IA_PROB", 60.0),
            patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False),
            patch.object(Config, "SHADOW_MODE_MIN", 50.0),
        ):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot,
                "BTC/USDT",
                "BUY",
                70.0,
                "SCOUT",
                False,
                "SHOCK DEMASIADO CERCA",
                _ctx(breakout_ready=True),
            )
        self.assertTrue(ok)
        self.assertTrue(shadow)

    def test_neutral_signal_no_execution(self):
        bot = _make_bot()
        with patch.object(Config, "DIRECTIONAL_COHERENCE_FILTER", False):
            ok, shadow, verdict, fp, fr = _plan_execution_mode(
                bot, "BTC/USDT", "NEUTRAL", 80.0, "OK", False, "", _ctx()
            )
        self.assertFalse(ok)


class TestResolveAuditVerdictAndStats(unittest.TestCase):
    def test_normal_pass(self):
        bot = _make_bot(get_audit_verdict=MagicMock(return_value="OK_BUY"))
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            verdict = _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 80.0, "⚪", 0.5, "REAL", _ctx(), True, "", 50.0, stats
            )
        self.assertEqual(verdict, "OK_BUY")
        self.assertEqual(stats["REAL"], 1)

    def test_veto_when_filter_not_passed(self):
        bot = _make_bot()
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            verdict = _resolve_audit_verdict_and_stats(
                bot,
                "BTC/USDT",
                "BUY",
                80.0,
                "⚪",
                0.5,
                "REAL",
                _ctx(),
                False,
                "bad volume",
                50.0,
                stats,
            )
        self.assertIn("VETO", verdict)
        self.assertIn("bad volume", verdict)
        self.assertEqual(stats["VETO"], 1)

    def test_veto_with_breakout_ready(self):
        bot = _make_bot()
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            verdict = _resolve_audit_verdict_and_stats(
                bot,
                "BTC/USDT",
                "BUY",
                80.0,
                "⚪",
                0.5,
                "REAL",
                _ctx(breakout_ready=True),
                False,
                "bad volume",
                50.0,
                stats,
            )
        self.assertIn("BREAKOUT READY", verdict)

    def test_kill_switch_over_95(self):
        bot = _make_bot()
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            verdict = _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 96.0, "⚪", 0.5, "REAL", _ctx(), True, "", 50.0, stats
            )
        self.assertIn("VETO", verdict)
        self.assertIn("ML_CONF", verdict)

    def test_ab_conflict_high_ml(self):
        bot = _make_bot()
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with (
            patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)),
            patch("core.signals.filters.open", return_value=io.StringIO()),
        ):
            verdict = _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 80.0, "⚪", 0.5, "REAL", _ctx(), False, "bad", 85.0, stats
            )
            self.assertIn("VETO", verdict)

    def test_ab_conflict_low_ml_passes(self):
        bot = _make_bot(get_audit_verdict=MagicMock(return_value="OK_LOW"))
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            verdict = _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 80.0, "⚪", 0.5, "REAL", _ctx(), True, "", 40.0, stats
            )
            self.assertIn("OK", verdict)

    def test_cooldown_overrides(self):
        bot = _make_bot()
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(True, 15)):
            verdict = _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 80.0, "⚪", 0.5, "REAL", _ctx(), True, "", 50.0, stats
            )
        self.assertIn("COOLDOWN", verdict)
        self.assertEqual(stats["VETO"], 1)

    def test_shadow_verdict_stats(self):
        bot = _make_bot(get_audit_verdict=MagicMock(return_value="SHADOW_OK"))
        stats = {"VETO": 0, "SHADOW": 0, "REAL": 0}
        with patch("core.signals.filters.is_symbol_in_cooldown", return_value=(False, 0)):
            _resolve_audit_verdict_and_stats(
                bot, "BTC/USDT", "BUY", 80.0, "⚪", 0.5, "REAL", _ctx(), True, "", 50.0, stats
            )
        self.assertEqual(stats["SHADOW"], 1)


if __name__ == "__main__":
    unittest.main()

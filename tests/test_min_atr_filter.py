"""Tests for MIN_ATR_PCT filter (Fase 1: Torniquete).

Validates that low-volatility symbols are vetoed and high-volatility ones pass.
"""

import unittest
from unittest.mock import MagicMock, patch

from config import Config
from core.signals.filters import _apply_entry_filters_and_adjust_prob


class TestMinAtrFilter(unittest.TestCase):
    """Test MIN_ATR_PCT filter behavior."""

    def _make_bot(self):
        bot = MagicMock()
        bot.db_lock = MagicMock()
        bot.brain = MagicMock()
        bot.brain.get_genetic_params.return_value = {}
        bot.brain.get_stats_by_trend.return_value = {}
        bot._get_market_regime.return_value = "BULL_TREND"
        bot._get_shock_distance_pct.return_value = (1.0, 0.0)
        bot.breakout_agent.evaluate_breakout.return_value = (False, None)
        bot.data_service = MagicMock()
        bot.data_service.sanitize_context.return_value = {}
        return bot

    def _make_ctx(self, atr_pct=0.003):
        return {
            "rsi": 55.0,
            "adx": 25.0,
            "atr_pct": atr_pct,
            "close": 100.0,
            "ema": 100.0,
            "ema_9": 100.5,
            "ema_21": 100.0,
            "ema_50": 99.5,
            "vol_rel": 1.5,
            "trend": "BULL",
            "atr": 1.0,
        }

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_low_atr_vetoed(self, mock_markov, mock_regime, mock_strategy):
        """ATR below MIN_ATR_PCT should be vetoed."""
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        bot = self._make_bot()
        ctx = self._make_ctx(atr_pct=0.002)

        with (
            patch.object(Config, "MIN_ATR_PCT", 0.005),
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", True),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                bot, "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, ctx, 1.5
            )

        self.assertFalse(result[1])
        self.assertIn("MIN_ATR_PCT", result[2])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_high_atr_passes(self, mock_markov, mock_regime, mock_strategy):
        """ATR above MIN_ATR_PCT should pass."""
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        bot = self._make_bot()
        ctx = self._make_ctx(atr_pct=0.01)

        with (
            patch.object(Config, "MIN_ATR_PCT", 0.005),
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", True),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                bot, "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, ctx, 1.5
            )

        self.assertTrue(result[1])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_filter_disabled(self, mock_markov, mock_regime, mock_strategy):
        """When filter is disabled, low ATR should not be vetoed."""
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        bot = self._make_bot()
        ctx = self._make_ctx(atr_pct=0.001)

        with (
            patch.object(Config, "MIN_ATR_PCT", 0.005),
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", False),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                bot, "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, ctx, 1.5
            )

        self.assertTrue(result[1])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_bull_trend_entry_veto_blocks_counter_trend_sell_when_enabled(
        self, mock_markov, mock_regime, mock_strategy
    ):
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        with (
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", False),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", True),
            patch.object(Config, "PAPER_MODE", True),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                self._make_bot(), "BTC/USDT", "BTC/USDT", None, "SELL", 70.0, self._make_ctx(), 1.5
            )

        self.assertFalse(result[1])
        self.assertIn("BULL_TREND_ENTRY_VETO", result[2])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_bull_trend_entry_veto_allows_aligned_buy_when_enabled(
        self, mock_markov, mock_regime, mock_strategy
    ):
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        with (
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", False),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", True),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                self._make_bot(), "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, self._make_ctx(), 1.5
            )

        self.assertTrue(result[1])
        self.assertNotIn("BULL_TREND_ENTRY_VETO", result[2])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_bull_trend_aligned_buy_remains_blocked_in_real_before_promotion(
        self, mock_markov, mock_regime, mock_strategy
    ):
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        with (
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", False),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", True),
            patch.object(Config, "BULL_TREND_ALIGNED_REAL_ENABLED", False),
            patch.object(Config, "PAPER_MODE", False),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                self._make_bot(), "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, self._make_ctx(), 1.5
            )

        self.assertFalse(result[1])
        self.assertIn("BULL_TREND_ENTRY_VETO", result[2])

    @patch("core.signals.filters.Strategy.check_entry_filters")
    @patch("core.signals.filters._resolve_btc_regime_adjustment")
    @patch("core.signals.filters._apply_markov_regime_weight")
    def test_bull_trend_entry_veto_allows_when_disabled(
        self, mock_markov, mock_regime, mock_strategy
    ):
        mock_strategy.return_value = (True, "OK", "BULL_TREND", {})
        mock_regime.return_value = (1.0, "BULL_ALIGNED", False)
        mock_markov.return_value = (1.0, "BULL_ALIGNED", False, True, "OK", "BULL_TREND")

        with (
            patch.object(Config, "MIN_ATR_PCT_FILTER_ENABLED", False),
            patch.object(Config, "BULL_TREND_ENTRY_VETO_ENABLED", False),
        ):
            result = _apply_entry_filters_and_adjust_prob(
                self._make_bot(), "BTC/USDT", "BTC/USDT", None, "BUY", 70.0, self._make_ctx(), 1.5
            )

        self.assertTrue(result[1])

    def test_config_defaults(self):
        """Config should have correct defaults."""
        self.assertGreaterEqual(Config.MIN_ATR_PCT, 0.0)
        self.assertTrue(hasattr(Config, "MIN_ATR_PCT_FILTER_ENABLED"))
        self.assertTrue(hasattr(Config, "BULL_TREND_ENTRY_VETO_ENABLED"))
        self.assertTrue(hasattr(Config, "HMM_RANGE_LEARNING_OVERRIDE_ENABLED"))
        self.assertTrue(hasattr(Config, "MAX_SHADOW_DIRECTIONAL_TRADES"))


if __name__ == "__main__":
    unittest.main()

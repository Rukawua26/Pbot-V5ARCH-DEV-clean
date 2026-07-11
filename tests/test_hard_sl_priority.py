"""Regression tests for Hard SL priority over ExitEngineV1 (TIME_DECAY_ESCAPE_VELOCITY).

Context: ExitEngineV1.evaluate_exit() was running BEFORE the Hard SL absolute check
in bot_guardian.py. This allowed trades with PnL -18.63% to close as TIME_DECAY_ESCAPE_VELOCITY
instead of "Hard SL (-3.5%)". The fix moves the Hard SL check before the exit engine.

These tests validate:
1. check_time_decay_exit() does NOT fire when PnL <= Hard SL (defense-in-depth).
2. check_flat_volatility_exit() does NOT fire when PnL <= Hard SL.
3. The guard name for the PnL floor is always consulted.
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from config import Config
from core.risk.exit_engine_v1 import ExitEngineV1
from core.time_utils import utc_now


class TestHardSLPriorityOverExitEngine(unittest.TestCase):
    """Hard SL absolute check must take priority over ExitEngineV1 time decay."""

    def test_time_decay_does_not_fire_when_pnl_below_hard_sl(self):
        """When PnL is worse than SHADOW_HARD_SL_PERCENT, check_time_decay_exit must
        return None so the guardian can close with Hard SL instead."""
        engine = ExitEngineV1(time_decay_bars=4, escape_velocity_pct=0.2)
        trade = {
            "pnl": -5.0,  # Worse than SHADOW_HARD_SL_PERCENT (-3.5)
            "open_time": "x",
            "is_shadow": True,
        }
        with patch.object(engine, "_bars_elapsed", return_value=10):
            with patch.object(Config, "SHADOW_HARD_SL_PERCENT", -3.5):
                result = engine.check_time_decay_exit(trade)
        self.assertIsNone(result)

    def test_time_decay_fires_when_pnl_above_hard_sl(self):
        """When PnL is above Hard SL threshold, time decay should fire normally."""
        engine = ExitEngineV1(time_decay_bars=4, escape_velocity_pct=0.2)
        trade = {
            "pnl": 0.1,  # Above Hard SL, below escape velocity
            "open_time": "x",
            "is_shadow": True,
        }
        with patch.object(engine, "_bars_elapsed", return_value=10):
            with patch.object(Config, "SHADOW_HARD_SL_PERCENT", -3.5):
                result = engine.check_time_decay_exit(trade)
        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], "TIME_DECAY_ESCAPE_VELOCITY")

    def test_time_decay_does_not_fire_for_real_hard_sl(self):
        """When PnL is worse than REAL_HARD_SL_PERCENT, time decay must not fire."""
        engine = ExitEngineV1(time_decay_bars=4, escape_velocity_pct=0.2)
        trade = {
            "pnl": -4.0,  # Worse than REAL_HARD_SL_PERCENT (-3.0)
            "open_time": "x",
            "is_shadow": False,
        }
        with patch.object(engine, "_bars_elapsed", return_value=10):
            with patch.object(Config, "REAL_HARD_SL_PERCENT", -3.0):
                result = engine.check_time_decay_exit(trade)
        self.assertIsNone(result)

    def test_flat_volatility_does_not_fire_when_pnl_below_hard_sl(self):
        """When PnL is worse than Hard SL, check_flat_volatility_exit must return None."""
        engine = ExitEngineV1(flat_time_decay_bars=3, flat_time_decay_atr_mult=0.5)
        trade = {
            "entry": 100.0,
            "open_time": (utc_now() - timedelta(hours=5)).isoformat(),
            "is_shadow": True,
            "pnl": -5.0,  # Worse than -3.5
        }
        with patch.object(Config, "SHADOW_HARD_SL_PERCENT", -3.5):
            result = engine.check_flat_volatility_exit(trade, 100.1, 1.0)
        self.assertIsNone(result)

    def test_flat_volatility_fires_when_pnl_above_hard_sl(self):
        """When PnL is above Hard SL, flat volatility should fire normally."""
        engine = ExitEngineV1(flat_time_decay_bars=3, flat_time_decay_atr_mult=0.5)
        trade = {
            "entry": 100.0,
            "open_time": (utc_now() - timedelta(hours=5)).isoformat(),
            "is_shadow": True,
            "pnl": -1.0,  # Above -3.5 Hard SL
        }
        with patch.object(Config, "SHADOW_HARD_SL_PERCENT", -3.5):
            result = engine.check_flat_volatility_exit(trade, 100.1, 1.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], "TIME_DECAY_FLAT_VOLATILITY")

    def test_evaluate_exit_does_not_return_time_decay_when_pnl_past_hard_sl(self):
        """Full evaluate_exit() must not return TIME_DECAY when PnL is past Hard SL."""
        engine = ExitEngineV1(time_decay_bars=4, escape_velocity_pct=0.2)
        trade = {
            "entry": 100.0,
            "open_time": (utc_now() - timedelta(hours=10)).isoformat(),
            "is_shadow": True,
            "pnl": -18.0,  # Far past Hard SL
            "peak_pnl": -18.0,
            "leverage": 1.0,
        }
        with patch.object(Config, "SHADOW_HARD_SL_PERCENT", -3.5):
            result = engine.evaluate_exit(trade, current_price=99.0, current_atr=1.0)
        # Must NOT be TIME_DECAY_ESCAPE_VELOCITY
        if result.get("should_exit"):
            self.assertNotIn("TIME_DECAY", result.get("reason", ""))


if __name__ == "__main__":
    unittest.main()

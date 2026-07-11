import unittest

from core.config.manager import _CONFIG_ENV_WARNINGS, Config, _env_bool, _env_float, _env_int
from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig


class ConfigPrecedenceTest(unittest.TestCase):
    def test_operational_stop_loss_atr_modifier_overrides_strategy_default(self):
        original_operational = getattr(OperationalConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        original_operational_alias = getattr(OperationalConfig, "ATR_SL_MULTIPLIER", 1.5)
        original_strategy = getattr(StrategyConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        original_strategy_alias = getattr(StrategyConfig, "ATR_SL_MULTIPLIER", 1.5)

        OperationalConfig.STOP_LOSS_ATR_MODIFIER = 2.25
        OperationalConfig.ATR_SL_MULTIPLIER = 2.25
        StrategyConfig.STOP_LOSS_ATR_MODIFIER = 1.5
        StrategyConfig.ATR_SL_MULTIPLIER = 1.5

        try:
            self.assertEqual(Config.STOP_LOSS_ATR_MODIFIER, 2.25)
            self.assertEqual(Config.ATR_SL_MULTIPLIER, 2.25)
        finally:
            OperationalConfig.STOP_LOSS_ATR_MODIFIER = original_operational
            OperationalConfig.ATR_SL_MULTIPLIER = original_operational_alias
            StrategyConfig.STOP_LOSS_ATR_MODIFIER = original_strategy
            StrategyConfig.ATR_SL_MULTIPLIER = original_strategy_alias

    def test_default_max_shadow_trades_allows_broader_exploration(self):
        self.assertGreaterEqual(Config.MAX_SHADOW_TRADES, 3)

    def test_config_env_parsers_fallback_on_invalid_values(self):
        import os
        from unittest.mock import patch

        _CONFIG_ENV_WARNINGS.clear()
        with patch.dict(
            os.environ,
            {
                "TEST_FLOAT_SETTING": "bad",
                "TEST_INT_SETTING": "also-bad",
                "TEST_BOOL_SETTING": "unknown",
            },
        ):
            self.assertEqual(_env_float("TEST_FLOAT_SETTING", 1.5), 1.5)
            self.assertEqual(_env_int("TEST_INT_SETTING", 4), 4)
            self.assertTrue(_env_bool("TEST_BOOL_SETTING", True))
        self.assertEqual(len(_CONFIG_ENV_WARNINGS), 3)
        self.assertFalse(any("bad" in warning for warning in _CONFIG_ENV_WARNINGS))
        self.assertFalse(any("unknown" in warning for warning in _CONFIG_ENV_WARNINGS))

    def test_sanitize_symbol_delegates_to_canonical_normalizer(self):
        self.assertEqual(Config.sanitize_symbol("btcusdt"), "BTC/USDT")
        self.assertEqual(Config.sanitize_symbol("eth/usdc:USDC"), "ETH/USDT")
        self.assertEqual(Config.sanitize_symbol("WLF I/USDT"), "WLFI/USDT")
        self.assertEqual(Config.sanitize_symbol("x"), "")

    def test_config_validation_rejects_inconsistent_thresholds(self):
        original_shadow = Config.SHADOW_MODE_MIN
        original_real = Config.REAL_MODE_THRESHOLD
        try:
            Config.SHADOW_MODE_MIN = 75.0
            Config.REAL_MODE_THRESHOLD = 70.0
            errors = Config.validate()
        finally:
            Config.SHADOW_MODE_MIN = original_shadow
            Config.REAL_MODE_THRESHOLD = original_real

        self.assertTrue(any("SHADOW_MODE_MIN" in error for error in errors))

    def test_config_validation_rejects_known_runtime_unsafe_thresholds(self):
        original_max_entry_sl = Config.MAX_ENTRY_SL_PCT
        original_shock_min_dist = Config.SHOCK_MIN_DIST_PCT
        try:
            Config.MAX_ENTRY_SL_PCT = 2.99
            Config.SHOCK_MIN_DIST_PCT = 0.21
            errors = Config.validate()
        finally:
            Config.MAX_ENTRY_SL_PCT = original_max_entry_sl
            Config.SHOCK_MIN_DIST_PCT = original_shock_min_dist

        self.assertTrue(any("MAX_ENTRY_SL_PCT" in error for error in errors))
        self.assertTrue(any("SHOCK_MIN_DIST_PCT" in error for error in errors))

import unittest
from contextlib import contextmanager

from config import Config
from core.config.operational import OperationalConfig
from tools.strategy import Strategy


@contextmanager
def _guardrail_config(max_entry_sl_pct, sl_modifier):
    original_operational_max = getattr(OperationalConfig, "MAX_ENTRY_SL_PCT", 3.0)
    original_operational_sl = getattr(OperationalConfig, "STOP_LOSS_ATR_MODIFIER", 2.0)
    original_config_attrs = {
        "MAX_ENTRY_SL_PCT": (
            "MAX_ENTRY_SL_PCT" in Config.__dict__,
            Config.__dict__.get("MAX_ENTRY_SL_PCT"),
        ),
        "STOP_LOSS_ATR_MODIFIER": (
            "STOP_LOSS_ATR_MODIFIER" in Config.__dict__,
            Config.__dict__.get("STOP_LOSS_ATR_MODIFIER"),
        ),
    }

    OperationalConfig.MAX_ENTRY_SL_PCT = max_entry_sl_pct
    OperationalConfig.STOP_LOSS_ATR_MODIFIER = sl_modifier
    Config.MAX_ENTRY_SL_PCT = max_entry_sl_pct
    Config.STOP_LOSS_ATR_MODIFIER = sl_modifier

    try:
        yield
    finally:
        OperationalConfig.MAX_ENTRY_SL_PCT = original_operational_max
        OperationalConfig.STOP_LOSS_ATR_MODIFIER = original_operational_sl
        for name, (had_own_attr, original_value) in original_config_attrs.items():
            if had_own_attr:
                setattr(Config, name, original_value)
            elif name in Config.__dict__:
                delattr(Config, name)


class EntryRiskGuardrailsTest(unittest.TestCase):
    def test_kava_veto_uses_configurable_max_entry_sl_pct(self):
        with _guardrail_config(max_entry_sl_pct=2.5, sl_modifier=1.5):
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="BUY",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=False,
                price=100.0,
                atr=1.5,
                side="BUY",
                regime="RANGO",
            )
            self.assertTrue(passed)
            self.assertEqual(reason, "Filter Pass (v118-PRO)")

    def test_kava_veto_uses_runtime_sl_modifier_and_genes(self):
        with _guardrail_config(max_entry_sl_pct=1.2, sl_modifier=1.5):
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="BUY",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=False,
                price=100.0,
                atr=1.0,
                side="BUY",
                regime="RANGO",
                modifier=0.8,
                genes={"sl_multiplier": 0.5},
            )
            self.assertTrue(passed)
            self.assertEqual(reason, "Filter Pass (v118-PRO)")

    def test_kava_default_3_0_passes_sl_2_8(self):
        with _guardrail_config(max_entry_sl_pct=3.0, sl_modifier=2.0):
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="SELL",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=True,
                price=100.0,
                atr=1.4,
                side="SELL",
                regime="DOWN",
            )
            self.assertTrue(passed)

    def test_kava_default_3_0_vetoes_sl_over_3_0(self):
        with _guardrail_config(max_entry_sl_pct=3.0, sl_modifier=2.0):
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="SELL",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=True,
                price=100.0,
                atr=1.8,
                side="SELL",
                regime="DOWN",
            )
            self.assertFalse(passed)
            self.assertIn("VETO_KAVA", reason)

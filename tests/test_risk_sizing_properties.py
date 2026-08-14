import math
import unittest
from unittest.mock import MagicMock, patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

FINITE_POSITIVE_FLOATS = st.floats(
    min_value=0.01,
    max_value=1_000_000.0,
    allow_nan=False,
    allow_infinity=False,
)


class RiskSizingPropertiesTest(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mocked_hyperopt:
                mocked_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.differing_executors],
    )
    @given(
        balance=FINITE_POSITIVE_FLOATS,
        price=FINITE_POSITIVE_FLOATS,
        leverage=st.integers(min_value=1, max_value=20),
        confidence=st.floats(
            min_value=0.0,
            max_value=100.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        atr_pct=st.floats(
            min_value=0.0,
            max_value=0.25,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    def test_shadow_sizing_is_finite_non_negative_and_margin_bounded(
        self, balance, price, leverage, confidence, atr_pct
    ):
        with (
            patch.object(self.engine, "hyperopt_enabled", False),
            patch("core.risk_engine.Config.USE_KELLY_SIZING", True, create=True),
            patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0),
            patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 5.0),
            patch("core.risk_engine.Config.MAX_RISK_USD", 10.0),
            patch("core.risk_engine.Config.STOP_LOSS_ATR_MODIFIER", 1.0),
        ):
            amount, notional = self.engine.calculate_position_size(
                balance,
                "BTC/USDT",
                price,
                leverage,
                {"prob_final": confidence, "atr_pct": atr_pct},
                is_shadow=True,
            )

        max_notional = balance * 0.05 * leverage
        self.assertTrue(math.isfinite(amount))
        self.assertTrue(math.isfinite(notional))
        self.assertGreaterEqual(amount, 0.0)
        self.assertGreaterEqual(notional, 0.0)
        self.assertLessEqual(notional, max_notional + 1e-9)
        self.assertAlmostEqual(amount * price, notional, delta=max(1e-9, notional * 1e-9))

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.differing_executors],
    )
    @given(
        entry=FINITE_POSITIVE_FLOATS,
        stop_distance_pct=st.floats(
            min_value=0.0001,
            max_value=0.5,
            allow_nan=False,
            allow_infinity=False,
        ),
        balance=FINITE_POSITIVE_FLOATS,
        risk_pct=st.floats(
            min_value=0.0001,
            max_value=0.05,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    def test_stop_based_sizing_respects_risk_budget(
        self, entry, stop_distance_pct, balance, risk_pct
    ):
        stop = entry * (1.0 - stop_distance_pct)
        with (
            patch("core.risk_engine.Config.MAX_RISK_USD", 0.0),
            patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 5.0),
            patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 0.0),
        ):
            amount, notional = self.engine.calculate_position_size_by_stop(
                balance,
                "BTC/USDT",
                entry,
                stop,
                leverage=10,
                is_shadow=True,
                risk_pct=risk_pct,
            )

        realized_risk = amount * abs(entry - stop)
        self.assertTrue(math.isfinite(amount))
        self.assertTrue(math.isfinite(notional))
        self.assertGreaterEqual(amount, 0.0)
        self.assertGreaterEqual(notional, 0.0)
        self.assertLessEqual(realized_risk, balance * risk_pct + 1e-8)


if __name__ == "__main__":
    unittest.main()

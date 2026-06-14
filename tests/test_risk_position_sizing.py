import unittest
from unittest.mock import MagicMock, patch

from core.risk_engine import RiskEngine


class DummyExchange:
    def __init__(self, precision_decimals=3):
        self.markets = {"BTC/USDT": {}}
        self.precision_decimals = precision_decimals
        self.load_markets = MagicMock()

    def amount_to_precision(self, _symbol, amount):
        return f"{float(amount):.{self.precision_decimals}f}"


class RiskPositionSizingTest(unittest.TestCase):
    def _engine(self):
        with patch("core.risk_engine.CrashPredictor"):
            return RiskEngine(brain=object())

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_uses_one_percent_risk_divided_by_stop_distance(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=98.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 5.0)
        self.assertEqual(notional, 500.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 12.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_forces_min_notional_when_risk_size_is_too_small(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=100.0,
            symbol="BTC/USDT",
            entry_price=10.0,
            stop_loss_price=5.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.2)
        self.assertEqual(notional, 12.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 12.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_rejects_min_notional_when_it_exceeds_real_risk_cap(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=100.0,
            symbol="BTC/USDT",
            entry_price=10.0,
            stop_loss_price=5.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -4)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 12.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_shadow_size_allows_min_notional_despite_real_risk_cap(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=100.0,
            symbol="BTC/USDT",
            entry_price=10.0,
            stop_loss_price=5.0,
            leverage=1,
            is_shadow=True,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.2)
        self.assertEqual(notional, 12.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 5.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_respects_margin_and_leverage_cap(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=99.0,
            leverage=2,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.0)
        self.assertEqual(notional, 100.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 12.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 5.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_rejects_min_notional_when_it_exceeds_margin_cap(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=10.0,
            symbol="BTC/USDT",
            entry_price=10.0,
            stop_loss_price=9.0,
            leverage=10,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -6)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 2.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_respects_absolute_max_risk_cap(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=98.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.0)
        self.assertEqual(notional, 100.0)

    def test_size_rejects_invalid_stop_distance(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=100.0,
            leverage=1,
            exchange=DummyExchange(),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -5)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 0.5)
    @patch("core.risk_engine.Config.STOP_LOSS_ATR_MODIFIER", 1.0)
    def test_kelly_rejects_post_precision_risk_increase(self):
        engine = self._engine()
        engine.hyperopt_enabled = False
        amount, code = engine.calculate_position_size(
            balance=100.0,
            symbol="BTC/USDT",
            price=100.0,
            leverage=1,
            context={"prob_final": 0.5, "atr_pct": 0.10},
            exchange=DummyExchange(precision_decimals=1),
        )

        self.assertEqual(amount, 0)
        self.assertEqual(code, -4)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 0.6)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_stop_sizing_rejects_post_precision_risk_increase(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=100.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=99.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=0),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -4)


if __name__ == "__main__":
    unittest.main()

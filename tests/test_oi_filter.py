"""Tests para el filtro de Open Interest Delta v118.3."""

import unittest

from core.signals.oi_filter import (
    _get_cached_oi,
    _oi_cache,
    _update_oi_cache,
    validate_signal_with_oi,
)


class OIValidationTests(unittest.TestCase):
    """Tests para la lógica de validación OI + precio."""

    def test_buy_confirmed_price_up_oi_up(self):
        result = validate_signal_with_oi("BUY", 0.02, 0.01)
        self.assertEqual(result, "CONFIRMED")

    def test_buy_veto_price_up_oi_down(self):
        result = validate_signal_with_oi("BUY", 0.02, -0.01)
        self.assertEqual(result, "VETO")

    def test_sell_confirmed_price_down_oi_up(self):
        result = validate_signal_with_oi("SELL", -0.02, 0.01)
        self.assertEqual(result, "CONFIRMED")

    def test_sell_veto_price_down_oi_down(self):
        result = validate_signal_with_oi("SELL", -0.02, -0.01)
        self.assertEqual(result, "VETO")

    def test_neutral_when_oi_is_none(self):
        result = validate_signal_with_oi("BUY", 0.02, None)
        self.assertEqual(result, "NEUTRAL")

    def test_neutral_when_oi_below_threshold(self):
        result = validate_signal_with_oi("BUY", 0.02, 0.001)
        self.assertEqual(result, "NEUTRAL")

    def test_neutral_when_signal_is_neutral(self):
        result = validate_signal_with_oi("NEUTRAL", 0.02, 0.01)
        self.assertEqual(result, "NEUTRAL")

    def test_buy_neutral_price_down(self):
        """BUY con precio bajando no entra en ninguna regla → NEUTRAL."""
        result = validate_signal_with_oi("BUY", -0.02, 0.01)
        self.assertEqual(result, "NEUTRAL")

    def test_sell_neutral_price_up(self):
        """SELL con precio subiendo no entra en ninguna regla → NEUTRAL."""
        result = validate_signal_with_oi("SELL", 0.02, 0.01)
        self.assertEqual(result, "NEUTRAL")


class OICacheTests(unittest.TestCase):
    """Tests para el cache de OI."""

    def setUp(self):
        _oi_cache.clear()

    def test_cache_stores_and_retrieves(self):
        _update_oi_cache("BTC/USDT", 50000.0)
        self.assertEqual(_get_cached_oi("BTC/USDT"), 50000.0)

    def test_cache_returns_none_for_unknown(self):
        self.assertIsNone(_get_cached_oi("UNKNOWN/USDT"))

    def test_cache_expired_returns_none(self):
        _oi_cache["BTC/USDT"] = {"oi": 50000.0, "ts": 0.0}  # Timestamp antiguo
        self.assertIsNone(_get_cached_oi("BTC/USDT"))


if __name__ == "__main__":
    unittest.main()

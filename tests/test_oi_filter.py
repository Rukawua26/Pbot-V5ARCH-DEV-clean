"""Tests para el filtro de Open Interest Delta v118.3."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.signals.oi_filter import (
    _get_cached_oi,
    _oi_cache,
    _update_oi_cache,
    fetch_oi_delta,
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

    @patch("core.signals.oi_filter.Config.OI_CACHE_TTL_SECONDS", 180)
    def test_fetch_oi_delta_uses_previous_cache_not_current_value(self):
        _update_oi_cache("BTC/USDT", 100.0)
        _update_oi_cache("BTC/USDT", 110.0)
        bot = SimpleNamespace(execution=object(), weight_tracker=None)

        delta, current = fetch_oi_delta(bot, "BTC/USDT")

        self.assertAlmostEqual(delta, 0.10)
        self.assertEqual(current, 110.0)


if __name__ == "__main__":
    unittest.main()

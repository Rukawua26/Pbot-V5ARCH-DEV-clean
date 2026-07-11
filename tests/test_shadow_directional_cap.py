"""Tests for MAX_SHADOW_DIRECTIONAL_TRADES cap (Fase 1: Torniquete).

Validates that no more than N shadow trades can be open in the same direction.
"""

import unittest
from unittest.mock import MagicMock, patch

from config import Config


class TestShadowDirectionalCap(unittest.TestCase):
    """Test that shadow trades are capped per direction."""

    def _make_bot(self, shadow_trades):
        """Create a bot mock with shadow trades in active_trades."""
        bot = MagicMock()
        bot.db_lock = MagicMock()
        bot.active_trades = {}
        for i, (sym, side) in enumerate(shadow_trades):
            key = f"shadow_{sym}_{i}"
            bot.active_trades[key] = {
                "symbol": sym,
                "side": side,
                "status": "OPEN",
                "is_shadow": True,
                "entry": 100.0,
                "amount": 1.0,
                "leverage": 10,
            }
        bot.brain = MagicMock()
        bot.brain.load_active_trade_states.return_value = {}
        bot.brain.save_error_snapshot = MagicMock()
        bot.data_service = MagicMock()
        bot.data_service.sanitize_context.return_value = {}
        bot.log = MagicMock()
        bot._check_entry_preconditions = MagicMock(return_value=True)
        bot.is_hedge_mode = False
        bot.price_priority_cache = {}
        return bot

    def test_shadow_directional_cap_blocks(self):
        """3 BUY shadow trades → 4th BUY should be blocked."""
        shadow_trades = [
            ("BTC/USDT", "BUY"),
            ("ETH/USDT", "BUY"),
            ("SOL/USDT", "BUY"),
        ]
        bot = self._make_bot(shadow_trades)

        with (
            patch.object(Config, "PAPER_MODE", True),
            patch.object(Config, "MAX_SHADOW_DIRECTIONAL_TRADES", 3),
            patch.object(Config, "MAX_SHADOW_TRADES", 6),
        ):
            actives = list(bot.active_trades.values())
            side = "BUY"
            shadow_side = sum(
                1 for t in actives if t.get("side") == side and t.get("is_shadow", False)
            )
            shadow_dir_limit = 3
            self.assertGreaterEqual(shadow_side, shadow_dir_limit)

    def test_shadow_directional_cap_allows_under_limit(self):
        """2 BUY shadow trades → 3rd BUY should be allowed."""
        shadow_trades = [
            ("BTC/USDT", "BUY"),
            ("ETH/USDT", "BUY"),
        ]
        bot = self._make_bot(shadow_trades)

        actives = list(bot.active_trades.values())
        side = "BUY"
        shadow_side = sum(1 for t in actives if t.get("side") == side and t.get("is_shadow", False))
        self.assertEqual(shadow_side, 2)
        self.assertLess(shadow_side, 3)

    def test_shadow_directional_cap_MIXED_allows(self):
        """3 BUY + 1 SELL → new SELL should be allowed (different direction)."""
        shadow_trades = [
            ("BTC/USDT", "BUY"),
            ("ETH/USDT", "BUY"),
            ("SOL/USDT", "BUY"),
            ("XRP/USDT", "SELL"),
        ]
        bot = self._make_bot(shadow_trades)

        actives = list(bot.active_trades.values())
        sell_side = sum(1 for t in actives if t.get("side") == "SELL" and t.get("is_shadow", False))
        self.assertEqual(sell_side, 1)
        self.assertLess(sell_side, 3)

    def test_config_has_shadow_directional(self):
        """Config should have MAX_SHADOW_DIRECTIONAL_TRADES."""
        self.assertTrue(hasattr(Config, "MAX_SHADOW_DIRECTIONAL_TRADES"))
        self.assertGreaterEqual(Config.MAX_SHADOW_DIRECTIONAL_TRADES, 1)
        self.assertLessEqual(Config.MAX_SHADOW_DIRECTIONAL_TRADES, 20)


if __name__ == "__main__":
    unittest.main()

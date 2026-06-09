import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.execution_adapters import ShadowExecutionAdapter
from core.trade_exit import close_trade
from core.trade_helpers import (
    _calculate_margin_used,
    _calculate_trade_pnl,
    _release_simulated_margin,
    _reserve_simulated_margin,
)


class _Brain:
    def __init__(self):
        self.logged = []
        self.deleted = []

    def save_active_trade_state(self, symbol, trade):
        return True

    def log_trade(self, payload):
        self.logged.append(payload)
        return 123

    def update_trade_context_result(self, **kwargs):
        return None

    def finalize_confidence_exit_audit(self, *args):
        return None

    def delete_active_trade_state(self, symbol):
        self.deleted.append(symbol)

    def evolve_genetics(self, symbol):
        return False

    def check_eureka_status(self, symbol):
        return "NONE", {}

    def get_recent_exit_confidence_stagnation(self, limit=10):
        return None


class _LiveExecution:
    def __init__(self):
        self.exchange = SimpleNamespace()
        self.logger = None


class ShadowWalletRuntimeTest(unittest.TestCase):
    def test_margin_reserve_and_release_are_idempotent(self):
        bot = SimpleNamespace(
            balance=20.0,
            available_balance=20.0,
            balance_lock=threading.Lock(),
        )
        trade = {"is_shadow": True, "margin_used": 2.0}

        ok, reason = _reserve_simulated_margin(bot, trade)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(bot.available_balance, 18.0)
        self.assertTrue(trade["margin_reserved"])

        self.assertTrue(_release_simulated_margin(bot, trade, 0.75))
        self.assertFalse(_release_simulated_margin(bot, trade, 0.75))
        self.assertAlmostEqual(bot.balance, 20.75)
        self.assertAlmostEqual(bot.available_balance, 20.75)

    def test_reserve_blocks_when_available_balance_is_insufficient(self):
        bot = SimpleNamespace(
            balance=20.0,
            available_balance=1.0,
            balance_lock=threading.Lock(),
        )
        ok, reason = _reserve_simulated_margin(bot, {"is_shadow": True, "margin_used": 2.0})
        self.assertFalse(ok)
        self.assertIn("SIM_BALANCE_INSUFFICIENT", reason)
        self.assertAlmostEqual(bot.available_balance, 1.0)

    def test_shadow_pnl_percent_uses_margin_base(self):
        pnl = _calculate_trade_pnl(
            side="BUY",
            entry_price=100.0,
            exit_price=110.0,
            amount=0.1,
            leverage=5,
            fee_rate=0.001,
            margin_used=_calculate_margin_used(10.0, 5),
            percent_on_margin=True,
        )
        self.assertAlmostEqual(pnl["gross_usd"], 1.0)
        self.assertAlmostEqual(pnl["fee_usd"], 0.021)
        self.assertAlmostEqual(pnl["net_usd"], 0.979)
        self.assertAlmostEqual(pnl["net_pct"], 48.95)

    def test_shadow_adapter_balance_provider(self):
        adapter = ShadowExecutionAdapter(_LiveExecution(), simulated_balance_provider=lambda: 42.5)
        self.assertEqual(adapter.get_balance(), 42.5)
        self.assertEqual(adapter.fetch_balance()["total"]["USDT"], 42.5)

    def test_close_trade_releases_shadow_margin_and_persists_margin_pnl(self):
        trade = {
            "symbol": "AAA/USDT",
            "side": "BUY",
            "entry": 100.0,
            "amount": 0.1,
            "open_time": "2026-01-01T00:00:00+00:00",
            "is_shadow": True,
            "leverage": 5,
            "margin_used": 2.0,
            "margin_reserved": True,
            "market_snapshot": {},
            "entry_confidence": 75.0,
        }
        bot = SimpleNamespace(
            active_trades={"AAA/USDT": trade},
            balance=20.0,
            available_balance=18.0,
            balance_lock=threading.Lock(),
            lock=threading.RLock(),
            db_lock=threading.RLock(),
            brain=_Brain(),
            data_service=SimpleNamespace(fetch_and_update_data=lambda *args: None),
            confidence_stagnation_lock_active=False,
            cooldown_pairs={},
            log=lambda *args: None,
            _get_market_regime=lambda: "RANGE",
            _check_recent_mfe_health=lambda: None,
        )

        with patch("core.trade_exit.send_telegram_msg"):
            close_trade(bot, "AAA/USDT", "HARD_SL_SHADOW", 110.0)

        self.assertNotIn("AAA/USDT", bot.active_trades)
        self.assertAlmostEqual(bot.balance, 20.979)
        self.assertAlmostEqual(bot.available_balance, 20.979)
        self.assertEqual(bot.brain.deleted, ["AAA/USDT"])
        self.assertAlmostEqual(bot.brain.logged[0]["pnl_usd"], 0.979)
        self.assertAlmostEqual(bot.brain.logged[0]["pnl_percent"], 48.95)


if __name__ == "__main__":
    unittest.main()

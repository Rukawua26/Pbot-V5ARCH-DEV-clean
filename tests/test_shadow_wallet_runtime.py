import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.active_trade_store import settle_simulated_trade_wallet
from core.bot_wallet_sync import sync_wallet
from core.execution_adapters import ShadowExecutionAdapter
from core.trade_exit import close_trade
from core.trade_helpers import (
    _calculate_margin_used,
    _calculate_trade_pnl,
    _release_simulated_margin,
    _reserve_simulated_margin,
    restore_simulated_available_balance,
    restore_simulated_wallet_state,
)


class _Brain:
    def __init__(self):
        self.logged = []
        self.deleted = []
        self.metadata = {}

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

    def get_metadata_json(self, key, default=None):
        return self.metadata.get(key, default)

    def set_metadata_json(self, key, value):
        self.metadata[key] = value
        return True

    def settle_simulated_trade_wallet(self, symbol, wallet_key, wallet_state):
        self.metadata[wallet_key] = wallet_state
        self.deleted.append(symbol)
        return True


class _LiveExecution:
    def __init__(self):
        self.exchange = SimpleNamespace()
        self.logger = None


class ShadowWalletRuntimeTest(unittest.TestCase):
    def test_atomic_settlement_updates_wallet_and_removes_active_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "wallet.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE active_trades_state (symbol TEXT PRIMARY KEY, state_data TEXT)"
            )
            conn.execute("CREATE TABLE system_meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO active_trades_state (symbol, state_data) VALUES (?, ?)",
                ("BTC/USDT", "{}"),
            )
            conn.commit()
            conn.close()

            brain = SimpleNamespace(_get_conn=lambda: sqlite3.connect(db_path))
            self.assertTrue(
                settle_simulated_trade_wallet(
                    brain,
                    "BTC/USDT",
                    "simulated_wallet_state_v1",
                    {"balance": 997.46, "daily_initial_balance": 1000.0},
                )
            )

            conn = sqlite3.connect(db_path)
            active_count = conn.execute("SELECT COUNT(*) FROM active_trades_state").fetchone()[0]
            wallet_raw = conn.execute(
                "SELECT value FROM system_meta WHERE key = ?",
                ("simulated_wallet_state_v1",),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(active_count, 0)
            self.assertEqual(json.loads(wallet_raw)["balance"], 997.46)

    def test_persisted_equity_is_restored_before_active_margin(self):
        brain = _Brain()
        bot = SimpleNamespace(
            brain=brain,
            balance=0.0,
            daily_initial_balance=0.0,
            active_trades={
                "BTC/USDT": {
                    "is_shadow": True,
                    "margin_used": 35.0,
                    "margin_released": False,
                }
            },
        )
        brain.metadata["simulated_wallet_state_v1"] = {
            "balance": 997.46,
            "daily_initial_balance": 1000.0,
        }

        self.assertTrue(restore_simulated_wallet_state(bot))

        self.assertEqual(bot.balance, 997.46)
        self.assertEqual(bot.daily_initial_balance, 1000.0)
        self.assertEqual(bot.available_balance, 962.46)

    def test_invalid_persisted_wallet_is_rejected(self):
        brain = _Brain()
        brain.metadata["simulated_wallet_state_v1"] = {"balance": "nan"}
        bot = SimpleNamespace(brain=brain, active_trades={})

        with self.assertRaisesRegex(RuntimeError, "SIMULATED_WALLET_STATE_INVALID"):
            restore_simulated_wallet_state(bot)

    def test_restored_shadow_margin_rebuilds_available_balance(self):
        bot = SimpleNamespace(
            balance=1000.0,
            available_balance=1000.0,
            active_trades={
                "BTC/USDT": {
                    "is_shadow": True,
                    "margin_used": 35.0,
                    "margin_released": False,
                }
            },
        )

        available = restore_simulated_available_balance(bot)

        self.assertEqual(available, 965.0)
        self.assertEqual(bot.available_balance, 965.0)

    @patch("core.bot_wallet_sync.Config.PAPER_MODE", True)
    def test_wallet_sync_skips_exchange_positions_in_paper(self):
        execution = SimpleNamespace(fetch_positions=MagicMock())
        bot = SimpleNamespace(execution=execution)

        sync_wallet(bot)

        execution.fetch_positions.assert_not_called()

    def test_margin_reserve_and_release_are_idempotent(self):
        bot = SimpleNamespace(
            balance=20.0,
            available_balance=20.0,
            balance_lock=threading.Lock(),
            brain=_Brain(),
        )
        trade = {"trade_key": "BTC/USDT", "is_shadow": True, "margin_used": 2.0}

        ok, reason = _reserve_simulated_margin(bot, trade)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(bot.available_balance, 18.0)
        self.assertTrue(trade["margin_reserved"])

        self.assertTrue(_release_simulated_margin(bot, trade, 0.75))
        self.assertFalse(_release_simulated_margin(bot, trade, 0.75))
        self.assertAlmostEqual(bot.balance, 20.75)
        self.assertAlmostEqual(bot.available_balance, 20.75)
        self.assertEqual(bot.brain.metadata["simulated_wallet_state_v1"]["balance"], 20.75)

    def test_failed_atomic_settlement_rolls_back_simulated_wallet(self):
        brain = _Brain()
        brain.settle_simulated_trade_wallet = MagicMock(return_value=False)
        bot = SimpleNamespace(
            balance=20.0,
            available_balance=18.0,
            daily_initial_balance=20.0,
            balance_lock=threading.Lock(),
            db_lock=threading.RLock(),
            brain=brain,
        )
        trade = {
            "trade_key": "BTC/USDT",
            "is_shadow": True,
            "margin_used": 2.0,
            "margin_released": False,
        }

        with self.assertRaisesRegex(RuntimeError, "SIMULATED_WALLET_SETTLEMENT_FAILED"):
            _release_simulated_margin(bot, trade, -0.5)

        self.assertEqual(bot.balance, 20.0)
        self.assertEqual(bot.available_balance, 18.0)
        self.assertFalse(trade["margin_released"])

    def test_concurrent_settlement_failure_does_not_rollback_success(self):
        brain = _Brain()
        brain.settle_simulated_trade_wallet = MagicMock(
            side_effect=lambda symbol, *_args: symbol == "BTC/USDT"
        )
        bot = SimpleNamespace(
            balance=20.0,
            available_balance=16.0,
            daily_initial_balance=20.0,
            balance_lock=threading.Lock(),
            db_lock=threading.RLock(),
            brain=brain,
        )
        success_trade = {
            "trade_key": "BTC/USDT",
            "is_shadow": True,
            "margin_used": 2.0,
            "margin_released": False,
        }
        failed_trade = {
            "trade_key": "ETH/USDT",
            "is_shadow": True,
            "margin_used": 2.0,
            "margin_released": False,
        }
        errors = []

        def release(trade, pnl):
            try:
                _release_simulated_margin(bot, trade, pnl)
            except RuntimeError as error:
                errors.append(str(error))

        first = threading.Thread(target=release, args=(success_trade, 1.0))
        second = threading.Thread(target=release, args=(failed_trade, -0.5))
        first.start()
        second.start()
        first.join()
        second.join()

        self.assertEqual(errors, ["SIMULATED_WALLET_SETTLEMENT_FAILED"])
        self.assertEqual(bot.balance, 21.0)
        self.assertEqual(bot.available_balance, 19.0)
        self.assertTrue(success_trade["margin_released"])
        self.assertFalse(failed_trade["margin_released"])

    @patch("core.trade_exit.Config.PAPER_MODE", True)
    def test_paper_close_quarantines_persisted_real_state(self):
        trade = {
            "trade_key": "BTC/USDT",
            "symbol": "BTC/USDT",
            "side": "BUY",
            "is_shadow": False,
            "simulated_real": False,
            "status": "OPEN",
        }
        bot = SimpleNamespace(
            active_trades={"BTC/USDT": trade},
            lock=threading.RLock(),
            log=MagicMock(),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
        )

        close_trade(bot, "BTC/USDT", "TEST", 100.0)

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertFalse(trade["closing_in_progress"])
        self.assertEqual(trade["status"], "OPEN")
        self.assertTrue(bot.halt_system_active)

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

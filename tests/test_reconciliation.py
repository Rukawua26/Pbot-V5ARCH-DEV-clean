import unittest
from datetime import UTC, datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from core.reconciliation import (
    generate_client_order_id,
    reconcile_bootstrap_state,
    recover_halt_if_exchange_consistent,
)


class ReconciliationTest(unittest.TestCase):
    def test_client_order_id_is_deterministic(self):
        a = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")
        b = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")
        self.assertEqual(a, b)
        # Nuevo formato: E_{hash}
        self.assertTrue(a.startswith("E_"), f"Expected 'E_' prefix, got: {a}")

    def test_integrity_lock_is_enabled_when_balance_diff_is_high(self):
        from config import Config as RealConfig

        original_paper_mode = RealConfig.PAPER_MODE

        try:
            RealConfig.PAPER_MODE = False
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [], fetch_open_orders=lambda: []
            )
            bot.get_current_balance = lambda: 80.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            from core.reconciliation import reconcile_bootstrap_state

            reconcile_bootstrap_state(bot)

            self.assertTrue(bot.integrity_lock_active, "integrity_lock_active should be True")
            self.assertTrue(bot.is_paused, "is_paused should be True")
        finally:
            RealConfig.PAPER_MODE = original_paper_mode

    def test_adopts_exchange_orphan_position(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "ETH/USDT:USDT",
                    "contracts": 0.5,
                    "side": "long",
                    "entryPrice": 3000,
                }
            ],
            fetch_open_orders=lambda: [],
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("ETH/USDT", bot.active_trades)
        trade = bot.active_trades["ETH/USDT"]
        self.assertTrue(trade.get("adopted_orphan", False))
        bot.execution.place_hard_sl.assert_called_once()
        self.assertIn("sl_client_order_id", trade)
        self.assertIn("client_order_id", bot.execution.place_hard_sl.call_args.kwargs)

    @patch("core.reconciliation.Config.PAPER_MODE", False)
    @patch("core.reconciliation.OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
    def test_real_unadoptable_orphan_halts_instead_of_ignoring(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "DOGE/USDT:USDT",
                    "contracts": 1.0,
                    "side": "long",
                    "entryPrice": 1.0,
                }
            ],
            fetch_open_orders=lambda: [],
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertNotIn("DOGE/USDT", bot.active_trades)
        bot.execution.place_hard_sl.assert_not_called()
        bot.brain.save_error_snapshot.assert_any_call(
            "DOGE/USDT",
            "REAL_ORPHAN_UNADOPTABLE_HALT",
            ANY,
        )

    @patch("core.reconciliation.Config.PAPER_MODE", False)
    def test_real_bootstrap_halts_when_open_orders_lookup_fails(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=MagicMock(side_effect=RuntimeError("exchange down")),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(getattr(bot, "halt_system_active", False))
        bot.brain.save_error_snapshot.assert_called()

    def test_keeps_pending_trade_if_open_order_exists_by_client_order_id(self):
        # Generar ID con nuevo formato
        entry_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": entry_coid,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [
                {
                    "id": "12345",
                    "symbol": "BTC/USDT",
                    "status": "open",
                    "clientOrderId": entry_coid,
                }
            ],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state.get("status"), "PENDING_EXCHANGE_OPEN")
        self.assertEqual(state.get("exchange_open_order_id"), "12345")
        bot.brain.save_error_snapshot.assert_not_called()
        bot.brain.delete_active_trade_state.assert_not_called()

    @patch("core.reconciliation.send_telegram_msg")
    def test_marks_lost_when_no_position_and_no_open_order(self, mocked_tg):
        stale_ts = (datetime.now(UTC) - timedelta(seconds=180)).isoformat()
        # Generar ID con nuevo formato
        missing_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "missing")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": missing_coid,
                "intent_created_at_utc": stale_ts,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.save_error_snapshot.assert_called_once()
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        self.assertEqual(bot.brain.save_error_snapshot.call_args[0][1], "INTENT_EXPIRED")
        mocked_tg.assert_called_once()

    def test_keeps_recent_pending_send_when_exchange_still_has_no_order(self):
        fresh_ts = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        # Generar ID con nuevo formato
        fresh_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "fresh")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": fresh_coid,
                "intent_created_at_utc": fresh_ts,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.pending_send_stale_seconds = 90
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertEqual(bot.active_trades["BTC/USDT"].get("status"), "PENDING_SEND")
        bot.brain.delete_active_trade_state.assert_not_called()
        bot.brain.save_error_snapshot.assert_not_called()

    def test_keeps_stale_pending_when_order_lookup_fails_transiently(self):
        stale_ts = (datetime.now(UTC) - timedelta(seconds=180)).isoformat()
        entry_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "lookup")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "ENTRY_ACK_UNKNOWN",
                "entry_client_order_id": entry_coid,
                "intent_created_at_utc": stale_ts,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=MagicMock(side_effect=RuntimeError("lookup down")),
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertEqual(bot.active_trades["BTC/USDT"].get("status"), "ORDER_LOOKUP_FAILED")
        bot.brain.delete_active_trade_state.assert_not_called()
        bot.brain.save_error_snapshot.assert_any_call(
            "BTC/USDT",
            "ORDER_LOOKUP_FAILED",
            {
                "entry_client_order_id": entry_coid,
                "error": "lookup down",
                "reconciliation_ts": bot.active_trades["BTC/USDT"].get("reconciled_at"),
            },
        )

    def test_recovers_pending_trade_using_explicit_order_lookup(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": "sai-v118-explicit",
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: {
                "id": "777",
                "symbol": "BTC/USDT",
                "status": "new",
                "clientOrderId": "sai-v118-explicit",
            },
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state.get("status"), "PENDING_EXCHANGE_OPEN")
        self.assertEqual(state.get("exchange_open_order_id"), "777")
        bot.brain.delete_active_trade_state.assert_not_called()

    def test_does_not_mark_lost_when_symbol_exists_in_open_orders(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "XRP/USDT": {
                "symbol": "XRP/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [
                {
                    "id": "open-xyz",
                    "symbol": "XRP/USDT",
                    "status": "open",
                }
            ],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("XRP/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_not_called()

    @patch("core.reconciliation.Config.PAPER_MODE", True)
    def test_reconciliation_aborts_without_mutating_state_when_positions_fail(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {"symbol": "BTC/USDT", "status": "OPEN", "is_shadow": False}
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=MagicMock(side_effect=RuntimeError("down")))
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        bot.brain.save_active_trade_state.assert_not_called()
        bot.brain.save_error_snapshot.assert_not_called()
        bot.brain.delete_active_trade_state.assert_not_called()

    @patch("core.reconciliation.Config.PAPER_MODE", False)
    def test_reconciliation_halts_real_mode_when_positions_fail(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {"symbol": "BTC/USDT", "status": "OPEN", "is_shadow": False}
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=MagicMock(side_effect=RuntimeError("down")))
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        bot.brain.save_error_snapshot.assert_called_once()
        bot.brain.delete_active_trade_state.assert_not_called()

    def test_reconciliation_skips_integrity_lock_when_balance_fetch_fails(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=lambda: [], fetch_open_orders=lambda: [])
        bot.get_current_balance = MagicMock(side_effect=RuntimeError("balance down"))
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertFalse(bot.integrity_lock_active)
        self.assertFalse(bot.is_paused)


class OrphanAdoptionTest(unittest.TestCase):
    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_rejected_below_min_size(self, mocked_tg):
        from core.config.operational import OperationalConfig

        original_min = getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
        original_max = getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "ETH/USDT:USDT",
                        "contracts": 0.001,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(),
                fetch_ticker=lambda s: {"last": 3000},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertNotIn("ETH/USDT", bot.active_trades)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_rejected_above_max_size(self, mocked_tg):
        from core.config.operational import OperationalConfig

        original_min = getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
        original_max = getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "ETH/USDT:USDT",
                        "contracts": 10.0,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(),
                fetch_ticker=lambda s: {"last": 3000},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertNotIn("ETH/USDT", bot.active_trades)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_adopted_with_dynamic_sl(self, mocked_tg):
        from core.config.operational import OperationalConfig

        original_min = getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
        original_max = getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "ETH/USDT:USDT",
                        "contracts": 0.5,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(return_value={"id": "sl-123"}),
                fetch_ticker=lambda s: {"last": 2950},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertIn("ETH/USDT", bot.active_trades)
            trade = bot.active_trades["ETH/USDT"]
            self.assertTrue(trade.get("adopted_orphan"))
            expected_sl = 3000 - (2950 * 0.02)
            self.assertAlmostEqual(trade["sl"], expected_sl, places=2)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_without_hard_sl_halts_runtime(self, mocked_tg):
        from core.config.operational import OperationalConfig

        original_min = getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
        original_max = getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.halt_system_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "ETH/USDT:USDT",
                        "contracts": 0.5,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(return_value=None),
                fetch_ticker=lambda s: {"last": 2950},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertIn("ETH/USDT", bot.active_trades)
            self.assertEqual(bot.active_trades["ETH/USDT"].get("status"), "ADOPTED_UNPROTECTED")
            self.assertTrue(bot.is_paused)
            self.assertTrue(bot.integrity_lock_active)
            self.assertTrue(bot.halt_system_active)
            bot.brain.save_error_snapshot.assert_called()
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_fallback_to_fixed_percentage_when_ticker_fails(self, mocked_tg):
        from core.config.operational import OperationalConfig

        original_min = getattr(OperationalConfig, "ORPHAN_ADOPTION_MIN_SIZE_USD", 10.0)
        original_max = getattr(OperationalConfig, "ORPHAN_ADOPTION_MAX_SIZE_USD", 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
            bot = SimpleNamespace()
            bot.lock = RLock()
            bot.db_lock = RLock()
            bot.active_trades = {}
            bot.balance = 100.0
            bot.is_paused = False
            bot.integrity_lock_active = False
            bot.log = MagicMock()
            bot.execution = SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "ETH/USDT:USDT",
                        "contracts": 0.5,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(return_value={"id": "sl-123"}),
                fetch_ticker=MagicMock(side_effect=RuntimeError("API error")),
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertIn("ETH/USDT", bot.active_trades)
            trade = bot.active_trades["ETH/USDT"]
            expected_sl = 3000 * 0.98
            self.assertAlmostEqual(trade["sl"], expected_sl, places=2)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max


class ChildOrderIdTest(unittest.TestCase):
    def test_child_id_is_deterministic(self):
        from core.reconciliation import generate_child_client_order_id

        a = generate_child_client_order_id("E_abc123def456abc123de", "SL")
        b = generate_child_client_order_id("E_abc123def456abc123de", "SL")
        self.assertEqual(a, b)

    def test_child_id_is_short(self):
        from core.reconciliation import generate_child_client_order_id

        for leg in ("SL", "TP", "UNKNOWN_LEG"):
            cid = generate_child_client_order_id("E_abc123def456abc123de", leg)
            self.assertLessEqual(
                len(cid),
                32,
                f"Child ID for leg {leg} exceeds 32 chars: {cid} (len={len(cid)})",
            )

    def test_child_id_does_not_exceed_binance_limit(self):
        from core.reconciliation import generate_child_client_order_id

        cid = generate_child_client_order_id("E_" + "x" * 28, "SL")
        self.assertLessEqual(
            len(cid),
            36,
            f"Child ID exceeds Binance 36 chars limit: {cid} (len={len(cid)})",
        )

    def test_child_id_uses_leg_prefix(self):
        from core.reconciliation import generate_child_client_order_id

        cid_sl = generate_child_client_order_id("E_abc", "SL")
        cid_tp = generate_child_client_order_id("E_abc", "TP")
        self.assertNotEqual(cid_sl, cid_tp)
        self.assertTrue(any(part.startswith("SL") for part in cid_sl.split("_")))
        self.assertTrue(any(part.startswith("TP") for part in cid_tp.split("_")))


class HaltRecoveryTest(unittest.TestCase):
    def _bot(self, active_trades=None, positions=None, balance=100.0):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = active_trades or {}
        bot.is_paused = True
        bot.integrity_lock_active = True
        bot.halt_system_active = True
        bot.balance = 0.0
        bot.daily_initial_balance = 0.0
        bot.execution = SimpleNamespace(fetch_positions=MagicMock(return_value=positions or []))
        bot.get_current_balance = MagicMock(return_value=balance)
        bot.log = MagicMock()
        bot.brain = SimpleNamespace(save_error_snapshot=MagicMock())
        return bot

    def test_recover_halt_requires_consecutive_flat_snapshots(self):
        bot = self._bot()

        ok1, msg1 = recover_halt_if_exchange_consistent(bot, required_snapshots=2)
        ok2, msg2 = recover_halt_if_exchange_consistent(bot, required_snapshots=2)

        self.assertFalse(ok1)
        self.assertIn("1/2", msg1)
        self.assertTrue(ok2)
        self.assertIn("RECOVERY_OK", msg2)
        self.assertFalse(bot.is_paused)
        self.assertFalse(bot.integrity_lock_active)
        self.assertFalse(bot.halt_system_active)
        self.assertEqual(bot.balance, 100.0)

    @patch("core.reconciliation.Config.HALT_RECOVERY_MAX_ATTEMPTS", 2)
    def test_recover_halt_blocks_after_max_attempts(self):
        bot = self._bot()

        ok1, _ = recover_halt_if_exchange_consistent(bot, required_snapshots=3)
        ok2, _ = recover_halt_if_exchange_consistent(bot, required_snapshots=3)
        ok3, msg3 = recover_halt_if_exchange_consistent(bot, required_snapshots=3)

        self.assertFalse(ok1)
        self.assertFalse(ok2)
        self.assertFalse(ok3)
        self.assertIn("MAX_ATTEMPTS", msg3)
        self.assertTrue(bot.halt_system_active)

    def test_recover_halt_blocks_when_local_real_trade_exists(self):
        bot = self._bot(active_trades={"BTC/USDT": {"symbol": "BTC/USDT", "is_shadow": False}})

        ok, msg = recover_halt_if_exchange_consistent(bot, required_snapshots=1)

        self.assertFalse(ok)
        self.assertIn("LOCAL_REAL", msg)
        self.assertTrue(bot.halt_system_active)

    def test_recover_halt_blocks_when_exchange_position_exists(self):
        bot = self._bot(positions=[{"symbol": "ETH/USDT:USDT", "contracts": 0.5, "side": "long"}])

        ok, msg = recover_halt_if_exchange_consistent(bot, required_snapshots=1)

        self.assertFalse(ok)
        self.assertIn("EXCHANGE_EXPOSURE", msg)
        self.assertTrue(bot.integrity_lock_active)


if __name__ == "__main__":
    unittest.main()

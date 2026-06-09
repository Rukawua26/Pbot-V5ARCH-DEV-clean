import unittest
from datetime import timedelta
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_runtime_ops import check_instinctive_safety
from core.reconciliation import reconcile_bootstrap_state
from core.time_utils import parse_datetime_utc
from core.trade_manager import execute_order


class AdvancedRuntimeFlowsTest(unittest.TestCase):
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=True)
    def test_execute_order_blocks_real_when_shadow_logger_halted(self, _mock_halted):
        bot = SimpleNamespace(log=MagicMock())

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "TRADING_HALTED_DB_ERROR")
        bot.log.assert_called_once()

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_blocks_symbol_from_tactical_matrix(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            _load_runtime_symbol_controls=lambda: {"blocked": {"BTC"}},
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "SYMBOL_BLOCKED_MATRIX")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_rejects_when_balance_below_min_notional(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            _load_runtime_symbol_controls=lambda: {
                "blocked": set(),
                "preferred": set(),
                "reduced": set(),
            },
            balance=0.0,
        )

        result = execute_order(
            bot,
            symbol="ETH/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "INSUFFICIENT_BALANCE_MIN_NOTIONAL")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_blocks_real_when_halt_system_active(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            integrity_lock_active=False,
            halt_system_active=True,
        )

        result = execute_order(
            bot,
            symbol="ETH/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "HALT_SYSTEM_ACTIVE")

    def test_instinctive_safety_forces_shadow_on_extreme_volatility(self):
        bot = SimpleNamespace(log=MagicMock())

        decision = check_instinctive_safety(bot, "SOL/USDT", {"atr_pct": 0.06})

        self.assertEqual(decision, "FORCE_SHADOW")
        bot.log.assert_called_once()

    def test_instinctive_safety_returns_ok_on_normal_context(self):
        bot = SimpleNamespace(log=MagicMock())

        decision = check_instinctive_safety(bot, "SOL/USDT", {"atr_pct": 0.01})

        self.assertEqual(decision, "OK")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_blocks_duplicate_when_recovery_pending(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "status": "PENDING_EXCHANGE_OPEN",
                }
            },
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "RECOVERY_PENDING_STATE")

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_timeout_after_pending_send_recovers_via_reconciliation(self, _mock_halted, _mock_tg):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.integrity_lock_active = False
        bot.balance = 500.0
        bot.available_balance = 500.0
        bot.is_paused = False
        bot.circuit_breaker_active = False
        bot.cooldown_pairs = {}
        bot.active_trades = {}
        bot.instance_uuid = "test-inst"
        bot._symbol_reduced_size_mult = 1.0
        bot.market_btc_change_tf = 0.0
        bot.ghost_model = object()
        bot._load_runtime_symbol_controls = lambda: {
            "blocked": set(),
            "reduced": set(),
        }
        bot._get_base_coin = lambda s: s.split("/")[0]
        bot.get_current_balance = lambda: 500.0
        bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})

        saved_states = {}

        def _save_active(symbol, state):
            saved_states[symbol] = dict(state)
            return True

        bot.brain = SimpleNamespace(
            get_genetic_params=lambda _symbol: {},
            get_stats_by_trend=lambda: {},
            save_active_trade_state=MagicMock(side_effect=_save_active),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(
                side_effect=lambda symbol: saved_states.pop(symbol, None)
            ),
        )
        bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
        bot.risk_engine = SimpleNamespace(
            calculate_position_size=lambda **kwargs: (1.0, 100.0),
            get_exit_levels=lambda **kwargs: (99.0, 120.0, "STD"),
            check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        )
        bot.execution = SimpleNamespace(
            exchange=object(),
            fetch_ticker=lambda _symbol: {"last": 100.0},
            set_leverage=MagicMock(),
            create_precision_order=MagicMock(side_effect=TimeoutError("network timeout")),
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={
                "atr_pct": 0.01,
                "trend": "RANGO",
                "spread": 0.0,
                "prob_final": 75.0,
            },
        )

        self.assertTrue(str(result).startswith("ERROR:"))
        self.assertIn("BTC/USDT", saved_states)
        self.assertEqual(saved_states["BTC/USDT"].get("status"), "PENDING_SEND")

        stale = parse_datetime_utc(saved_states["BTC/USDT"]["intent_created_at_utc"]) - timedelta(
            seconds=180
        )
        saved_states["BTC/USDT"]["intent_created_at_utc"] = stale.isoformat()

        bot.active_trades = {"BTC/USDT": dict(saved_states["BTC/USDT"])}
        reconcile_bootstrap_state(bot)

        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        self.assertGreaterEqual(bot.brain.save_error_snapshot.call_count, 1)
        self.assertEqual(
            bot.brain.save_error_snapshot.call_args_list[-1][0][1],
            "INTENT_EXPIRED",
        )

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_tracks_partial_fill_state(self, _mock_halted, _mock_tg):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.balance = 500.0
        bot.available_balance = 500.0
        bot.is_paused = False
        bot.circuit_breaker_active = False
        bot.cooldown_pairs = {}
        bot.active_trades = {}
        bot.instance_uuid = "test-inst"
        bot._symbol_reduced_size_mult = 1.0
        bot.market_btc_change_tf = 0.0
        bot.ghost_model = object()
        bot._load_runtime_symbol_controls = lambda: {"blocked": set(), "reduced": set()}
        bot._get_base_coin = lambda s: s.split("/")[0]
        bot.get_current_balance = lambda: 500.0
        bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})

        bot.brain = SimpleNamespace(
            get_genetic_params=lambda _symbol: {},
            get_stats_by_trend=lambda: {},
            save_active_trade_state=MagicMock(return_value=True),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )
        bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
        bot.risk_engine = SimpleNamespace(
            calculate_position_size=lambda **kwargs: (10.0, 1000.0),
            get_exit_levels=lambda **kwargs: (99.0, 120.0, "STD"),
            check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        )
        bot.execution = SimpleNamespace(
            exchange=object(),
            fetch_ticker=lambda _symbol: {"last": 100.0},
            set_leverage=MagicMock(),
            create_precision_order=MagicMock(
                return_value={
                    "id": "order-1",
                    "status": "open",
                    "filled": 4.0,
                    "average": 100.5,
                }
            ),
            place_hard_sl=MagicMock(return_value={"id": "sl-1"}),
        )

        result = execute_order(
            bot,
            symbol="SOL/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={
                "atr_pct": 0.01,
                "trend": "RANGO",
                "spread": 0.0,
                "prob_final": 75.0,
            },
        )

        self.assertEqual(result, "OK")
        trade = bot.active_trades.get("SOL/USDT")
        self.assertIsNotNone(trade)
        self.assertEqual(trade.get("status"), "PARTIAL_FILL_PENDING")
        self.assertTrue(trade.get("partial_fill_pending"))
        self.assertEqual(trade.get("amount"), 4.0)
        self.assertEqual(trade.get("requested_amount"), 10.0)
        self.assertEqual(trade.get("remaining_amount"), 6.0)

    @patch("core.trade_entry.Config.PAPER_MODE", True)
    @patch("core.trade_entry.Config.MAX_DIRECTIONAL_TRADES", 1)
    @patch("core.trade_entry.Config.MAX_SHADOW_TRADES", 2)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_paper_directional_limit_degrades_to_shadow(self, _mock_halted, _mock_tg):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.balance = 500.0
        bot.available_balance = 500.0
        bot.is_paused = False
        bot.circuit_breaker_active = False
        bot.cooldown_pairs = {}
        bot.active_trades = {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "OPEN",
                "sector": "OTHE",
            }
        }
        bot.instance_uuid = "test-inst"
        bot.last_entry_open_ts = 0.0
        bot.last_shadow_signal_ts = 0.0
        bot._symbol_reduced_size_mult = 1.0
        bot.market_btc_change_tf = 0.0
        bot.ghost_model = object()
        bot._load_runtime_symbol_controls = lambda: {"blocked": set(), "reduced": set()}
        bot._get_base_coin = lambda s: s.split("/")[0]
        bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})

        bot.brain = SimpleNamespace(
            get_genetic_params=lambda _symbol: {},
            get_stats_by_trend=lambda: {},
            load_active_trade_states=lambda: {},
            save_active_trade_state=MagicMock(return_value=True),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )
        bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
        bot.risk_engine = SimpleNamespace(
            calculate_position_size=lambda **kwargs: (1.0, 100.0),
            get_exit_levels=lambda **kwargs: (99.0, 120.0, "STD"),
            check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        )
        bot.execution = SimpleNamespace(
            exchange=object(),
            fetch_ticker=lambda _symbol: {"last": 100.0},
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={
                "atr_pct": 0.01,
                "trend": "RANGO",
                "spread": 0.0,
                "prob_final": 75.0,
            },
        )

        self.assertEqual(result, "OK_DEGRADED: MAX_DIRECTIONAL_DEGRADED")
        self.assertTrue(bot.active_trades["BTC/USDT"].get("is_shadow"))

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.Config.MAX_SHADOW_TRADES", 1)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_discards_signal_alert_when_shadow_limit_blocks_entry(
        self, _mock_halted, _mock_tg
    ):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.integrity_lock_active = False
        bot.halt_system_active = False
        bot.balance = 500.0
        bot.available_balance = 500.0
        bot.is_paused = False
        bot.circuit_breaker_active = False
        bot.cooldown_pairs = {}
        bot.active_trades = {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "BUY",
                "is_shadow": True,
                "status": "OPEN",
                "sector": "OTHE",
            }
        }
        bot.instance_uuid = "test-inst"
        bot.last_entry_open_ts = 0.0
        bot.last_shadow_signal_ts = 0.0
        bot._symbol_reduced_size_mult = 1.0
        bot.market_btc_change_tf = 0.0
        bot.ghost_model = object()
        bot._load_runtime_symbol_controls = lambda: {"blocked": set(), "reduced": set()}
        bot._get_base_coin = lambda s: s.split("/")[0]
        bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})

        bot.brain = SimpleNamespace(
            get_genetic_params=lambda _symbol: {},
            get_stats_by_trend=lambda: {},
            log_signal_alert=MagicMock(),
            update_signal_alert_status=MagicMock(),
            save_error_snapshot=MagicMock(),
        )
        bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
        bot.risk_engine = SimpleNamespace(
            calculate_position_size=lambda **kwargs: (1.0, 100.0),
            get_exit_levels=lambda **kwargs: (99.0, 120.0, "STD"),
            check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        )
        bot.execution = SimpleNamespace(exchange=object())

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=True,
            context={
                "atr_pct": 0.01,
                "trend": "RANGO",
                "spread": 0.0,
                "prob_final": 60.0,
            },
        )

        self.assertEqual(result, "MAX_SHADOW")
        bot.brain.log_signal_alert.assert_called_once()
        bot.brain.update_signal_alert_status.assert_called_once()
        self.assertEqual(bot.brain.update_signal_alert_status.call_args.args[1], "DISCARDED")


if __name__ == "__main__":
    unittest.main()

import threading
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from config import Config
from core.bot_guardian import run_guardian_loop
from core.bot_trade_monitor import monitor_open_trades
from core.strategy.agents.ghost_agent import GhostAgent
from core.time_utils import utc_now, utc_now_iso


class _PredictProbaModel:
    def predict_proba(self, _features):
        return [[0.35, 0.65]]


class SmartExitConfidenceGuardrailsTest(unittest.TestCase):
    def test_ghost_agent_missing_model_returns_neutral_vote(self):
        self.assertEqual(GhostAgent().vote({"model": None}), 50.0)

    @patch("core.strategy.agents.ghost_agent.os.path.exists", return_value=True)
    @patch("core.strategy.agents.ghost_agent.safe_pickle_load")
    def test_ghost_agent_assigns_selected_model_after_pickle_load(self, mocked_load, _exists):
        model = _PredictProbaModel()
        mocked_load.return_value = {"n_samples": 7, "rf": model, "feature_cols": ["rsi"]}

        agent = GhostAgent()
        loaded = agent.load_trained_model()

        self.assertIs(loaded["rf"], model)
        self.assertIs(agent.model, model)

    @patch("core.strategy.agents.ghost_agent.os.path.exists", return_value=True)
    @patch("core.strategy.agents.ghost_agent.safe_pickle_load", return_value={"n_samples": 7})
    def test_ghost_agent_keeps_model_none_when_pickle_has_no_predictor(self, _load, _exists):
        agent = GhostAgent()

        agent.load_trained_model()

        self.assertIsNone(agent.model)

    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_uses_shadow_threshold_and_entry_confidence_fallback(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 1.1,
            "pnl": 0.0,
            "peak_pnl": 0.0,
            "open_time": utc_now_iso(),
            "is_shadow": True,
            "entry_confidence": 80.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
        }

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 1.0}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.execution = SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 1.0}))
        bot.exit_engine = SimpleNamespace(
            evaluate_exit=MagicMock(return_value={"should_exit": False, "reason": "HOLD"})
        )
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(False, "CONF_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        bot.risk_engine.should_abort_trade.assert_not_called()
        bot.close_trade.assert_not_called()

    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_closes_when_configured_tp_price_is_hit(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 1.1,
            "pnl": 0.0,
            "peak_pnl": 0.0,
            "open_time": utc_now_iso(),
            "is_shadow": True,
            "entry_confidence": 80.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
        }

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 1.12}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.execution = SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 1.12}))
        bot.exit_engine = SimpleNamespace(
            evaluate_exit=MagicMock(return_value={"should_exit": False, "reason": "HOLD"})
        )
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(False, "CONF_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        bot.close_trade.assert_called_once_with("XPL/USDT", "TAKE_PROFIT", 1.12)

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_amends_exchange_hard_sl_after_local_tighten(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 0.0,
            "pnl": 1.5,
            "peak_pnl": 1.5,
            "open_time": utc_now_iso(),
            "is_shadow": False,
            "entry_confidence": 80.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
            "sl_exchange_order_id": "old-sl",
            "sl_client_order_id": "SL_OLD",
        }

        def _tighten(trade, **_kwargs):
            trade["sl"] = 1.01
            return {"should_exit": False, "reason": "BREAKEVEN_GUARD_ARMED"}

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.db_lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 1.02}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.execution = SimpleNamespace(
            fetch_ticker=MagicMock(return_value={"last": 1.02}),
            place_hard_sl=MagicMock(return_value={"id": "new-sl"}),
            cancel_order=MagicMock(return_value={"id": "old-sl", "status": "canceled"}),
        )
        bot.exit_engine = SimpleNamespace(evaluate_exit=MagicMock(side_effect=_tighten))
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(False, "CONF_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
            save_active_trade_state=MagicMock(return_value=True),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        bot.execution.place_hard_sl.assert_called_once()
        bot.execution.cancel_order.assert_called_once_with("XPL/USDT", "old-sl")
        self.assertEqual(trade["sl_exchange_order_id"], "new-sl")
        self.assertEqual(trade["hard_sl_price"], 1.01)

    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_passes_shadow_threshold_after_cooldown(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 1.1,
            "pnl": 0.0,
            "peak_pnl": 0.0,
            "open_time": (utc_now() - timedelta(minutes=20)).isoformat(),
            "is_shadow": True,
            "entry_confidence": 80.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
        }

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 1.0}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.execution = SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 1.0}))
        bot.exit_engine = SimpleNamespace(
            evaluate_exit=MagicMock(return_value={"should_exit": False, "reason": "HOLD"})
        )
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(False, "CONF_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        bot.risk_engine.should_abort_trade.assert_called_once_with(
            80.0,
            80.0,
            Config.SMART_EXIT_THRESHOLD_SHADOW,
        )
        bot.close_trade.assert_not_called()

    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_uses_live_price_for_bailout_exit(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 1.1,
            "pnl": 0.0,
            "peak_pnl": 0.0,
            "open_time": (utc_now() - timedelta(minutes=20)).isoformat(),
            "is_shadow": False,
            "entry_confidence": 80.0,
            "current_confidence": 50.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
        }

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 1.0}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.execution = SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 1.0}))
        bot.exit_engine = SimpleNamespace(
            evaluate_exit=MagicMock(return_value={"should_exit": False, "reason": "HOLD"})
        )
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(True, "CONF_DEGRADED_37.5%")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        bot.close_trade.assert_called_once()
        self.assertEqual(bot.close_trade.call_args.args[2], 1.0)

    @patch("core.bot_guardian.time.sleep", return_value=None)
    def test_guardian_pre_sl_warning_uses_configured_hard_sl(self, _sleep_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "entry": 1.0,
            "amount": 1.0,
            "sl": 0.9,
            "tp": 1.1,
            "pnl": 0.0,
            "peak_pnl": 0.0,
            "open_time": utc_now_iso(),
            "is_shadow": True,
            "entry_confidence": 80.0,
            "leverage": 10,
            "market_snapshot": {},
            "trailing_active": False,
        }

        bot = SimpleNamespace()
        bot.is_running = True
        bot.lock = threading.Lock()
        bot.price_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.live_prices = {"XPLUSDT": 0.999}
        bot.log = MagicMock()
        bot.close_trade = MagicMock()
        bot.sync_wallet = MagicMock()
        bot._guardian_stats = {"bailout_count": 0, "loops": 0, "work_s": 0.0, "sleep_s": 0.0}
        bot._exit_eval_last_log = {}
        bot.ghost_model = None
        bot.ghost_model_type = None
        bot.execution = SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 0.999}))
        bot.exit_engine = SimpleNamespace(
            evaluate_exit=MagicMock(return_value={"should_exit": False, "reason": "HOLD"})
        )
        bot.risk_engine = SimpleNamespace(
            should_abort_trade=MagicMock(return_value=(False, "CONF_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.brain = SimpleNamespace(
            pending_model_update=False,
            upsert_confidence_exit_audit=MagicMock(return_value=1),
        )

        def _monitor_once():
            bot.is_running = False

        bot.monitor_open_trades = _monitor_once

        run_guardian_loop(bot)

        self.assertTrue(trade["pre_sl_warning_logged"])
        self.assertFalse(
            any("Guardian error" in str(call.args[0]) for call in bot.log.call_args_list)
        )
        bot.close_trade.assert_not_called()

    @patch("core.bot_trade_monitor.Strategy.analyze")
    def test_monitor_updates_trade_current_confidence(self, analyze_mock):
        trade = {
            "symbol": "XPL/USDT",
            "side": "BUY",
            "open_time": "2024-01-01T00:00:00+00:00",
            "entry_confidence": 80.0,
        }
        analyze_mock.return_value = ("BUY", "REAL", None, 62.0, {}, {})

        bot = SimpleNamespace()
        bot.lock = threading.Lock()
        bot.db_lock = threading.Lock()
        bot.active_trades = {"XPL/USDT": trade}
        bot.brain = SimpleNamespace(upsert_confidence_exit_audit=MagicMock(return_value=1))
        bot.ghost_model = None
        bot.scaler = None
        bot.market_btc_change_tf = 0.0
        bot.data_service = SimpleNamespace(
            fetch_and_update_data=MagicMock(return_value=SimpleNamespace(empty=False))
        )
        bot.risk_engine = SimpleNamespace(
            check_signal_integrity=MagicMock(return_value=(False, "INTEGRITY_OK")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(False, "NOT_FEE_NOISE_REASON")
            ),
        )
        bot.close_trade = MagicMock()
        bot.log = MagicMock()

        monitor_open_trades(bot)

        self.assertEqual(trade["current_confidence"], 62.0)
        bot.close_trade.assert_not_called()

    @patch("core.bot_trade_monitor.Strategy.analyze")
    def test_monitor_defers_degraded_exit_when_move_does_not_cover_fees(self, analyze_mock):
        trade = {
            "symbol": "TRX/USDT",
            "side": "BUY",
            "entry": 100.0,
            "amount": 1.0,
            "open_time": "2024-01-01T00:00:00+00:00",
            "entry_confidence": 80.0,
        }
        market_df = MagicMock()
        market_df.empty = False
        market_df.__getitem__.return_value.iloc.__getitem__.return_value = 100.1
        analyze_mock.return_value = (
            "BUY",
            "REAL",
            None,
            22.1,
            {"regime": "CALM", "trend": "UP"},
            {"MT": 26.0, "SR": 18.0, "G": 22.0},
        )

        bot = SimpleNamespace()
        bot.lock = threading.Lock()
        bot.db_lock = threading.Lock()
        bot.active_trades = {"TRX/USDT": trade}
        bot.brain = SimpleNamespace(upsert_confidence_exit_audit=MagicMock(return_value=1))
        bot.ghost_model = MagicMock()
        bot.scaler = None
        bot.bootstrap_heuristic_mode = False
        bot.market_btc_change_tf = 0.0
        bot.data_service = SimpleNamespace(fetch_and_update_data=MagicMock(return_value=market_df))
        bot.risk_engine = SimpleNamespace(
            check_signal_integrity=MagicMock(return_value=(True, "CONFIDENCE_FLOOR_VIOLATED_22.1")),
            should_defer_confidence_exit_for_fee_noise=MagicMock(
                return_value=(True, "FEE_NOISE_GROSS=0.100%_FLOOR=0.200%")
            ),
        )
        bot.close_trade = MagicMock()
        bot.log = MagicMock()

        monitor_open_trades(bot)

        bot.close_trade.assert_not_called()
        self.assertIn("last_confidence_trace", trade)

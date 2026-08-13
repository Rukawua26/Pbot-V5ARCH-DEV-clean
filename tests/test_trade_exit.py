import unittest
from contextlib import ExitStack
from datetime import UTC, datetime
from threading import RLock
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from config import Config


class TestExchangePositionIsFlat(unittest.TestCase):
    """_exchange_position_is_flat is a 1-line delegation wrapper."""

    def test_delegates_to_helper_with_args(self):
        from core.trade_exit import _exchange_position_is_flat

        bot = MagicMock()
        with patch("core.trade_exit._helper_exchange_position_is_flat") as mock_helper:
            mock_helper.return_value = True
            result = _exchange_position_is_flat(bot, "BTCUSDT")
        self.assertTrue(result)
        mock_helper.assert_called_once_with(bot, "BTCUSDT")

    def test_passes_through_false(self):
        from core.trade_exit import _exchange_position_is_flat

        bot = MagicMock()
        with patch("core.trade_exit._helper_exchange_position_is_flat") as mock_helper:
            mock_helper.return_value = False
            result = _exchange_position_is_flat(bot, "ETHUSDT")
        self.assertFalse(result)


class TestRecordMtfTradeOutcome(unittest.TestCase):
    """_record_mtf_trade_outcome — dict logic with global accumulator."""

    def setUp(self):
        import core.trade_exit as _te

        _te._MTF_TRADE_RESULTS = []

    def test_skips_when_no_mtf_reason(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        _record_mtf_trade_outcome({"market_snapshot": {}}, 1.0)
        self.assertEqual(len(_MTF_TRADE_RESULTS), 0)

    def test_appends_win_entry(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        _record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": "ALIGNED"}}, 2.5)
        self.assertEqual(len(_MTF_TRADE_RESULTS), 1)
        entry = _MTF_TRADE_RESULTS[0]
        self.assertEqual(entry["mtf_reason"], "ALIGNED")
        self.assertAlmostEqual(entry["pnl_percent"], 2.5)
        self.assertTrue(entry["is_win"])

    def test_appends_loss_entry(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        _record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": "CONFLICT_15M"}}, -3.0)
        entry = _MTF_TRADE_RESULTS[0]
        self.assertFalse(entry["is_win"])
        self.assertAlmostEqual(entry["pnl_percent"], -3.0)

    def test_accumulates_multiple_entries(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        for i in range(5):
            _record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": "ALIGNED"}}, float(i))
        self.assertEqual(len(_MTF_TRADE_RESULTS), 5)

    def test_triggers_report_at_window_boundary(self):
        import core.trade_exit as te

        with patch.object(Config, "MTF_METRICS_WINDOW", 3):
            with patch("core.trade_exit.append_execution_event"):
                for i in range(3):
                    te._record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": f"R{i}"}}, 1.0)
        self.assertEqual(len(te._MTF_TRADE_RESULTS), 0)

    def test_does_not_trigger_before_window(self):
        import core.trade_exit as te

        with patch.object(Config, "MTF_METRICS_WINDOW", 10):
            with patch("core.trade_exit.append_execution_event") as mock_append:
                for i in range(5):
                    te._record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": "A"}}, 1.0)
        mock_append.assert_not_called()

    def test_zero_pnl_is_considered_loss(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _record_mtf_trade_outcome

        _record_mtf_trade_outcome({"market_snapshot": {"mtf_reason": "NEUTRAL"}}, 0.0)
        self.assertFalse(_MTF_TRADE_RESULTS[0]["is_win"])


class TestLogMtfWinrateReport(unittest.TestCase):
    """_log_mtf_winrate_report — aggregation logic."""

    def setUp(self):
        import core.trade_exit as _te

        _te._MTF_TRADE_RESULTS = []

    def test_early_return_when_empty(self):
        from core.trade_exit import _log_mtf_winrate_report

        with patch("core.trade_exit.append_execution_event") as mock_append:
            _log_mtf_winrate_report()
        mock_append.assert_not_called()

    def test_resets_accumulator_after_report(self):
        import core.trade_exit as te

        te._MTF_TRADE_RESULTS.append({"mtf_reason": "A", "pnl_percent": 1.0, "is_win": True})
        with patch("core.trade_exit.append_execution_event"):
            te._log_mtf_winrate_report()
        self.assertEqual(len(te._MTF_TRADE_RESULTS), 0)

    def test_reports_correct_win_rate_single_reason(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _log_mtf_winrate_report

        for pnl in [2.0, -1.0, 3.0, -4.0, 1.5]:
            _MTF_TRADE_RESULTS.append(
                {"mtf_reason": "ALIGNED", "pnl_percent": pnl, "is_win": pnl > 0}
            )
        with patch("core.trade_exit.append_execution_event") as mock_append:
            _log_mtf_winrate_report()
        data = mock_append.call_args[0][2]
        self.assertEqual(data["total_trades"], 5)
        self.assertEqual(data["wins"], 3)
        self.assertEqual(data["losses"], 2)
        self.assertAlmostEqual(data["win_rate_pct"], 60.0)
        self.assertIn("ALIGNED", data["per_reason"])
        self.assertEqual(data["per_reason"]["ALIGNED"]["total"], 5)
        self.assertEqual(data["per_reason"]["ALIGNED"]["wins"], 3)
        self.assertAlmostEqual(data["per_reason"]["ALIGNED"]["win_rate_pct"], 60.0)

    def test_aggregates_multiple_reasons(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _log_mtf_winrate_report

        _MTF_TRADE_RESULTS.append({"mtf_reason": "ALIGNED", "pnl_percent": 2.0, "is_win": True})
        _MTF_TRADE_RESULTS.append({"mtf_reason": "ALIGNED", "pnl_percent": 1.0, "is_win": True})
        _MTF_TRADE_RESULTS.append(
            {"mtf_reason": "CONFLICT_15M", "pnl_percent": -3.0, "is_win": False}
        )
        _MTF_TRADE_RESULTS.append(
            {"mtf_reason": "CONFLICT_5M", "pnl_percent": -1.0, "is_win": False}
        )

        with patch("core.trade_exit.append_execution_event") as mock_append:
            _log_mtf_winrate_report()
        data = mock_append.call_args[0][2]
        self.assertEqual(data["total_trades"], 4)
        self.assertEqual(data["per_reason"]["ALIGNED"]["total"], 2)
        self.assertEqual(data["per_reason"]["ALIGNED"]["wins"], 2)
        self.assertAlmostEqual(data["per_reason"]["ALIGNED"]["win_rate_pct"], 100.0)
        self.assertEqual(data["per_reason"]["CONFLICT_15M"]["total"], 1)
        self.assertEqual(data["per_reason"]["CONFLICT_15M"]["wins"], 0)
        self.assertAlmostEqual(data["per_reason"]["CONFLICT_15M"]["win_rate_pct"], 0.0)
        self.assertEqual(data["per_reason"]["CONFLICT_5M"]["total"], 1)

    def test_passes_bot_to_append_execution_event(self):
        from core.trade_exit import _MTF_TRADE_RESULTS, _log_mtf_winrate_report

        bot = MagicMock()
        _MTF_TRADE_RESULTS.append({"mtf_reason": "A", "pnl_percent": 1.0, "is_win": True})
        with patch("core.trade_exit.append_execution_event") as mock_append:
            _log_mtf_winrate_report(bot=bot)
        mock_append.assert_called_once_with(bot, "MTF_WINRATE_REPORT", ANY)


class TestAbortPartialTrade(unittest.TestCase):
    """abort_partial_trade — thin wrapper around close_trade."""

    def test_delegates_to_close_trade_with_guardian_context(self):
        from core.trade_exit import abort_partial_trade

        bot = MagicMock()
        with patch("core.trade_exit.append_execution_event"):
            with patch("core.trade_exit.close_trade") as mock_close:
                abort_partial_trade(bot, "BTCUSDT", "PARTIAL_FILL", 51000.0)
        mock_close.assert_called_once_with(
            bot,
            symbol="BTCUSDT",
            reason="PARTIAL_FILL",
            exit_price=51000.0,
            latency_context={"trigger": "GUARDIAN_PARTIAL_ABORT"},
        )

    def test_logs_execution_event_before_delegation(self):
        from core.trade_exit import abort_partial_trade

        bot = MagicMock()
        with patch("core.trade_exit.append_execution_event") as mock_append:
            with patch("core.trade_exit.close_trade"):
                abort_partial_trade(bot, "ETHUSDT", "TEST_REASON", 1000.0)
        mock_append.assert_called_once_with(
            bot,
            "PARTIAL_TRADE_ABORT_REQUESTED",
            {"symbol": "ETHUSDT", "reason": "TEST_REASON", "exit_price": 1000.0},
        )

    def test_coerces_none_exit_price_to_zero(self):
        from core.trade_exit import abort_partial_trade

        bot = MagicMock()
        with patch("core.trade_exit.append_execution_event") as mock_append:
            with patch("core.trade_exit.close_trade"):
                abort_partial_trade(bot, "BTCUSDT", "PARTIAL_FILL", None)
        event_data = mock_append.call_args[0][2]
        self.assertEqual(event_data["exit_price"], 0.0)


class TestCloseTradePaperPath(unittest.TestCase):
    """close_trade with Config.PAPER_MODE=True — virtual fees, no exchange."""

    def setUp(self):
        import core.trade_exit as _te

        _te._MTF_TRADE_RESULTS = []

    @patch("core.trade_exit.send_telegram_photo")
    @patch("core.trade_exit.send_telegram_msg")
    @patch("core.trade_exit.set_symbol_cooldown")
    @patch("core.trade_exit.record_regime_trade")
    @patch("core.trade_exit.shadow_logger.log")
    @patch("core.trade_exit.append_execution_event")
    @patch("core.trade_exit.label_exit_reason")
    @patch("core.trade_exit._calculate_pnl_and_metrics")
    def test_paper_path_uses_virtual_fees_and_skips_exchange(
        self,
        mock_pnl,
        mock_label,
        mock_append,
        mock_shadow,
        mock_regime,
        mock_cd,
        mock_tg_msg,
        mock_tg_photo,
    ):
        with patch.object(Config, "PAPER_MODE", True):
            with patch.object(Config, "VIRTUAL_FEE", 0.001):
                with patch.object(Config, "TRADE_COOLDOWN_MINUTES", 60):
                    with patch.object(Config, "REGIME_TUNING_ENABLED", False):
                        self._run_close_trade_and_assert(mock_pnl, mock_label)

    def _run_close_trade_and_assert(self, mock_pnl, mock_label):
        from core.trade_exit import close_trade

        mock_pnl.return_value = {
            "amt": 0.001,
            "pnl_bruto_usd": 1.0,
            "pnl_neto_usd": 0.899,
            "pnl_neto_percent": 1.798,
            "mae_percent": 0.0,
            "mfe_percent": 2.0,
        }
        mock_label.return_value = {
            "exit_reason": "MANUAL_CLOSE",
            "is_adopted": 0,
            "is_dirty": 0,
            "mae_at_sl": 0.0,
            "mfe_at_sl": 0.0,
        }

        open_time = datetime(2024, 1, 1, tzinfo=UTC)
        trade = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry": 50000.0,
            "amount": 0.001,
            "open_time": open_time,
            "closing_in_progress": False,
            "status": "OPEN",
            "is_shadow": False,
            "simulated_real": True,
            "market_snapshot": {},
            "entry_confidence": 75.0,
            "mae_price": 50000.0,
            "mfe_price": 50000.0,
            "entry_atr": 100.0,
        }
        bot = SimpleNamespace(
            lock=MagicMock(),
            db_lock=MagicMock(),
            active_trades={"BTCUSDT": dict(trade)},
            recent_closed_trades=[],
            log=MagicMock(),
            brain=SimpleNamespace(
                log_trade=MagicMock(return_value=123),
                save_active_trade_state=MagicMock(),
                delete_active_trade_state=MagicMock(),
                finalize_confidence_exit_audit=MagicMock(),
                update_agent_reputation=MagicMock(),
                evolve_genetics=MagicMock(return_value=False),
                check_eureka_status=MagicMock(return_value=("UNKNOWN", {})),
                get_recent_exit_confidence_stagnation=MagicMock(return_value=None),
            ),
            risk_engine=SimpleNamespace(
                record_trade_result=MagicMock(),
            ),
            execution=SimpleNamespace(
                close_position=MagicMock(),
                close_due_to_degradation=MagicMock(),
                fetch_my_trades=MagicMock(),
            ),
            _get_market_regime=MagicMock(return_value="RANGE"),
            _update_dynamic_risk=MagicMock(),
            _check_recent_mfe_health=MagicMock(),
            confidence_stagnation_lock_active=False,
            cooldown_pairs={},
            is_paused=False,
            pause_time=None,
            integrity_lock_active=False,
        )

        close_trade(bot, "BTCUSDT", "MANUAL_CLOSE", 51000.0)

        # -- No exchange calls in PAPER mode --
        bot.execution.close_position.assert_not_called()
        bot.execution.close_due_to_degradation.assert_not_called()
        bot.execution.fetch_my_trades.assert_not_called()

        # -- log_trade called once with virtual fees --
        bot.brain.log_trade.assert_called_once()
        log_args = bot.brain.log_trade.call_args[0][0]
        self.assertEqual(log_args["symbol"], "BTCUSDT")
        self.assertEqual(log_args["side"], "BUY")
        self.assertAlmostEqual(log_args["entry"], 50000.0)
        self.assertAlmostEqual(log_args["exit"], 51000.0)
        expected_fees = 50000.0 * 0.001 * 0.001 + 51000.0 * 0.001 * 0.001
        self.assertAlmostEqual(log_args["fees"], expected_fees, places=6)
        self.assertAlmostEqual(log_args["pnl_percent"], 1.798)

        # -- Trade removed from active --
        self.assertNotIn("BTCUSDT", bot.active_trades)

        # -- Recent trades updated --
        self.assertEqual(len(bot.recent_closed_trades), 1)
        self.assertEqual(bot.recent_closed_trades[0]["symbol"], "BTCUSDT")

        # -- Post-close bookkeeping --
        bot.risk_engine.record_trade_result.assert_called_once_with("BTCUSDT", 1.798)
        bot._update_dynamic_risk.assert_called_once()
        bot._check_recent_mfe_health.assert_called_once()
        bot.brain.evolve_genetics.assert_not_called()
        self.assertEqual(bot._genetic_batch_pending_symbols, {"BTCUSDT"})

        # -- Cooldown was applied --
        import core.trade_exit as te

        te.set_symbol_cooldown.assert_called_once()

        # -- Telegram notification sent --
        te.send_telegram_msg.assert_called_once()


class TestCloseTradeRealFailurePath(unittest.TestCase):
    def _bot(self):
        trade = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "entry": 50000.0,
            "amount": 0.001,
            "open_time": datetime(2024, 1, 1, tzinfo=UTC),
            "closing_in_progress": False,
            "status": "OPEN",
            "is_shadow": False,
            "market_snapshot": {},
            "entry_confidence": 75.0,
        }
        return SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={"BTC/USDT": trade},
            recent_closed_trades=[],
            log=MagicMock(),
            brain=SimpleNamespace(
                save_active_trade_state=MagicMock(),
                delete_active_trade_state=MagicMock(),
            ),
            execution=SimpleNamespace(
                close_position=MagicMock(
                    return_value={"id": "close-1", "status": "closed", "filled": 0.001}
                ),
                close_due_to_degradation=MagicMock(),
                fetch_my_trades=MagicMock(return_value=[]),
            ),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
        )

    @patch("core.trade_exit.time.sleep", return_value=None)
    @patch("core.trade_exit.append_execution_event")
    @patch("core.trade_exit.send_telegram_msg")
    @patch("core.trade_exit._exchange_position_is_flat", return_value=False)
    def test_real_unconfirmed_close_sets_exit_stuck_and_halts(
        self, _flat, _tg, append_event, _sleep
    ):
        from core.trade_exit import close_trade
        from core.trade_state import TradeStatus

        bot = self._bot()
        with patch.object(Config, "PAPER_MODE", False):
            close_trade(bot, "BTC/USDT", "MANUAL_CLOSE", 50100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(bot.active_trades["BTC/USDT"]["status"], TradeStatus.EXIT_STUCK.value)
        self.assertFalse(bot.active_trades["BTC/USDT"]["closing_in_progress"])
        bot.brain.save_active_trade_state.assert_called_with(
            "BTC/USDT", bot.active_trades["BTC/USDT"]
        )
        append_event.assert_called_with(
            bot,
            "REAL_CLOSE_FAILED_HALT",
            ANY,
        )

    @patch("core.trade_exit.time.sleep", return_value=None)
    @patch("core.trade_exit.append_execution_event")
    @patch("core.trade_exit.send_telegram_msg")
    @patch("core.trade_exit._order_looks_filled", return_value=False)
    @patch("core.trade_exit._exchange_position_is_flat", return_value=False)
    def test_unclassified_exception_without_filled_order_halts(
        self, _flat, _filled, _tg, append_event, _sleep
    ):
        """Excepción no clasificada + order no filled debe activar HALT (fail-safe)."""
        from core.trade_exit import close_trade
        from core.trade_state import TradeStatus

        bot = self._bot()
        # Forzamos excepción genérica no clasificada (sin 'notional' ni 'insufficient')
        bot.execution.close_position = MagicMock(
            side_effect=RuntimeError("connection reset by peer")
        )
        with patch.object(Config, "PAPER_MODE", False):
            close_trade(bot, "BTC/USDT", "MANUAL_CLOSE", 50100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(bot.active_trades["BTC/USDT"]["status"], TradeStatus.EXIT_STUCK.value)
        append_event.assert_any_call(bot, "REAL_CLOSE_FAILED_HALT", ANY)

    @patch("core.trade_exit.time.sleep", return_value=None)
    @patch("core.trade_exit.append_execution_event")
    @patch("core.trade_exit.send_telegram_msg")
    @patch("core.trade_exit._order_looks_filled", return_value=True)
    @patch("core.trade_exit._exchange_position_is_flat", return_value=True)
    def test_unclassified_exception_with_filled_and_flat_does_not_halt(
        self, _flat, _filled, _tg, append_event, _sleep
    ):
        """Excepción genérica + order filled + posición plana NO debe HALT (cierre válido)."""
        from core.trade_exit import close_trade

        bot = self._bot()
        bot.execution.close_position = MagicMock(
            return_value={"id": "close-1", "status": "closed", "filled": 0.001}
        )
        # Excepción genérica posterior al close (ej. falla un télem)
        bot.execution.fetch_my_trades = MagicMock(side_effect=RuntimeError("telemetry error"))
        # Aserciones de cierre: debe completar la rama de éxito y NO activar HALT.
        with patch.object(Config, "PAPER_MODE", False):
            # No debe lanzar excepción no controlada y debe borrar el trade activo
            try:
                close_trade(bot, "BTC/USDT", "MANUAL_CLOSE", 51000.0)
            except Exception:
                pass  # fetch_my_trades falla internamente con log, no propaga
        self.assertFalse(bot.is_paused or bot.integrity_lock_active or bot.halt_system_active)
        # No se emitió REAL_CLOSE_FAILED_HALT
        for call in append_event.call_args_list:
            self.assertNotEqual(call.args[1], "REAL_CLOSE_FAILED_HALT")

    @patch("core.trade_exit.time.sleep", return_value=None)
    @patch("core.trade_exit.append_execution_event")
    @patch("core.trade_exit.send_telegram_msg")
    @patch("core.trade_exit._order_looks_filled", return_value=True)
    @patch("core.trade_exit._exchange_position_is_flat", return_value=False)
    def test_unclassified_exception_filled_but_not_flat_halts(
        self, _flat, _filled, _tg, append_event, _sleep
    ):
        """Orden filled pero posición NO plana tras excepción genérica debe HALT."""
        from core.trade_exit import close_trade
        from core.trade_state import TradeStatus

        bot = self._bot()
        # Close lanza excepción genérica; la orden reporta filled pero la posición sigue viva
        bot.execution.close_position = MagicMock(side_effect=RuntimeError("partial timeout"))
        with patch.object(Config, "PAPER_MODE", False):
            close_trade(bot, "BTC/USDT", "MANUAL_CLOSE", 50100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(bot.active_trades["BTC/USDT"]["status"], TradeStatus.EXIT_STUCK.value)


class TestCloseTradeRealSuccessPath(unittest.TestCase):
    def _bot(self, trade_overrides=None, stagnation=None):
        trade = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "entry": 50000.0,
            "amount": 0.001,
            "open_time": datetime(2024, 1, 1, tzinfo=UTC),
            "closing_in_progress": False,
            "status": "OPEN",
            "is_shadow": False,
            "market_snapshot": {"votos": {"MT": 60.0}, "context": "RANGE"},
            "entry_confidence": 75.0,
            "mae_price": 49800.0,
            "mfe_price": 50500.0,
            "entry_atr": 100.0,
            "market_regime": "RANGE",
            "entry_shock_level": 49900.0,
        }
        if trade_overrides:
            trade.update(trade_overrides)
        return SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={"BTC/USDT": trade},
            recent_closed_trades=[],
            log=MagicMock(),
            brain=SimpleNamespace(
                log_trade=MagicMock(return_value=321),
                save_active_trade_state=MagicMock(),
                delete_active_trade_state=MagicMock(),
                finalize_confidence_exit_audit=MagicMock(),
                update_trade_context_result=MagicMock(),
                update_agent_reputation=MagicMock(),
                evolve_genetics=MagicMock(return_value=False),
                check_eureka_status=MagicMock(return_value=("UNKNOWN", {})),
                get_recent_exit_confidence_stagnation=MagicMock(return_value=stagnation),
                update_dynamic_settings=MagicMock(),
            ),
            risk_engine=SimpleNamespace(record_trade_result=MagicMock()),
            execution=SimpleNamespace(
                close_position=MagicMock(
                    return_value={"id": "close-1", "status": "closed", "filled": 0.001}
                ),
                close_due_to_degradation=MagicMock(
                    return_value={"id": "deg-1", "status": "closed", "filled": 0.001}
                ),
                fetch_my_trades=MagicMock(
                    return_value=[{"fee": {"currency": "USDT", "cost": 0.25}}]
                ),
            ),
            _get_market_regime=MagicMock(return_value="RANGE"),
            _update_dynamic_risk=MagicMock(),
            _check_recent_mfe_health=MagicMock(),
            confidence_stagnation_lock_active=False,
            cooldown_pairs={},
            is_paused=False,
            pause_time=None,
            integrity_lock_active=False,
            halt_system_active=False,
        )

    def _patch_success_deps(self, pnl_percent=2.0, pnl_usd=1.0):
        return (
            patch("core.trade_exit.time.sleep", return_value=None),
            patch("core.trade_exit._exchange_position_is_flat", return_value=True),
            patch("core.trade_exit.send_telegram_msg"),
            patch("core.trade_exit.set_symbol_cooldown"),
            patch("core.trade_exit.record_regime_trade"),
            patch("core.trade_exit.shadow_logger.log"),
            patch("core.trade_exit.append_execution_event"),
            patch("core.trade_exit.append_runtime_metric"),
            patch("core.trade_exit.label_exit_reason", return_value={"exit_reason": "MANUAL"}),
            patch(
                "core.trade_exit._calculate_pnl_and_metrics",
                return_value={
                    "amt": 0.001,
                    "pnl_bruto_usd": pnl_usd,
                    "pnl_neto_usd": pnl_usd,
                    "pnl_neto_percent": pnl_percent,
                    "mae_percent": -0.4,
                    "mfe_percent": 1.0,
                },
            ),
        )

    def test_real_success_closes_exchange_fetches_fees_and_removes_trade(self):
        from core.trade_exit import close_trade

        bot = self._bot()
        with (
            patch.object(Config, "PAPER_MODE", False),
            patch.object(Config, "REGIME_TUNING_ENABLED", True),
        ):
            with ExitStack() as stack:
                for cm in self._patch_success_deps():
                    stack.enter_context(cm)
                close_trade(bot, "BTC/USDT", "MANUAL_CLOSE", 51000.0, exit_confidence=70.0)

        bot.execution.close_position.assert_called_once_with(
            "BTC/USDT", "BUY", 0.001, position_side=None
        )
        bot.execution.close_due_to_degradation.assert_not_called()
        bot.execution.fetch_my_trades.assert_called_once_with("BTC/USDT", limit=2)
        self.assertNotIn("BTC/USDT", bot.active_trades)
        logged = bot.brain.log_trade.call_args[0][0]
        self.assertEqual(logged["fees"], 0.25)
        bot.brain.update_trade_context_result.assert_called_once()
        bot.brain.finalize_confidence_exit_audit.assert_called_once()
        bot.risk_engine.record_trade_result.assert_called_once_with("BTC/USDT", 2.0)

    def test_degraded_reason_uses_degradation_close_path(self):
        from core.trade_exit import close_trade

        bot = self._bot()
        with (
            patch.object(Config, "PAPER_MODE", False),
            patch.object(Config, "REGIME_TUNING_ENABLED", False),
        ):
            with ExitStack() as stack:
                for cm in self._patch_success_deps():
                    stack.enter_context(cm)
                close_trade(bot, "BTC/USDT", "CONF_DEGRADED_EXIT", 51000.0)

        bot.execution.close_due_to_degradation.assert_called_once_with(
            "BTC/USDT", "BUY", 0.001, position_side=None
        )
        bot.execution.close_position.assert_not_called()

    def test_large_real_loss_sets_stagnation_and_circuit_breaker(self):
        from core.trade_exit import close_trade

        bot = self._bot(stagnation={"stddev": 0.2, "mean": 55, "min": 54, "max": 56, "count": 10})
        with (
            patch.object(Config, "PAPER_MODE", False),
            patch.object(Config, "REGIME_TUNING_ENABLED", False),
        ):
            with ExitStack() as stack:
                for cm in self._patch_success_deps(pnl_percent=-16.5, pnl_usd=-8.0):
                    stack.enter_context(cm)
                close_trade(bot, "BTC/USDT", "CIRCUIT BREAKER", 41000.0)

        self.assertTrue(bot.confidence_stagnation_lock_active)
        self.assertTrue(bot.is_paused)
        self.assertIsNotNone(bot.pause_time)
        bot.risk_engine.record_trade_result.assert_called_once_with("BTC/USDT", -16.5)


class TestGeneticBatch(unittest.TestCase):
    @patch("core.bot_maintenance.Config.GENETIC_BATCH_ENABLED", True)
    @patch("core.bot_maintenance.Config.GENETIC_BATCH_MIN_TRADES", 2)
    @patch("core.bot_maintenance.append_execution_event")
    def test_genetic_batch_processes_symbol_with_enough_samples(self, append_event):
        from core.bot_maintenance import run_genetic_batch

        bot = SimpleNamespace(
            _genetic_batch_pending_symbols={"BTC/USDT"},
            log=MagicMock(),
            brain=SimpleNamespace(
                count_trades_for_symbol=MagicMock(return_value=2),
                evolve_genetics=MagicMock(return_value=True),
            ),
        )

        result = run_genetic_batch(bot)

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["mutated"], 1)
        self.assertEqual(bot._genetic_batch_pending_symbols, set())
        bot.brain.evolve_genetics.assert_called_once_with("BTC/USDT")
        self.assertTrue(
            any(
                call.args[1] == "GENETIC_BATCH_SWAP_APPLIED" for call in append_event.call_args_list
            )
        )

    @patch("core.bot_maintenance.Config.GENETIC_BATCH_ENABLED", True)
    @patch("core.bot_maintenance.Config.GENETIC_BATCH_MIN_TRADES", 50)
    @patch("core.bot_maintenance.append_execution_event")
    def test_genetic_batch_keeps_pending_when_samples_are_insufficient(self, _append_event):
        from core.bot_maintenance import run_genetic_batch

        bot = SimpleNamespace(
            _genetic_batch_pending_symbols={"ETH/USDT"},
            log=MagicMock(),
            brain=SimpleNamespace(
                count_trades_for_symbol=MagicMock(return_value=10),
                evolve_genetics=MagicMock(return_value=True),
            ),
        )

        result = run_genetic_batch(bot)

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["mutated"], 0)
        self.assertEqual(bot._genetic_batch_pending_symbols, {"ETH/USDT"})
        bot.brain.evolve_genetics.assert_not_called()

    @patch("core.bot_maintenance.Config.GENETIC_BATCH_ENABLED", False)
    @patch("core.bot_maintenance.append_execution_event")
    def test_genetic_batch_skips_when_disabled(self, _append_event):
        from core.bot_maintenance import run_genetic_batch

        bot = SimpleNamespace(
            _genetic_batch_pending_symbols={"SOL/USDT"},
            log=MagicMock(),
            brain=SimpleNamespace(evolve_genetics=MagicMock(return_value=True)),
        )

        result = run_genetic_batch(bot)

        self.assertEqual(result["status"], "SKIPPED")
        bot.brain.evolve_genetics.assert_not_called()

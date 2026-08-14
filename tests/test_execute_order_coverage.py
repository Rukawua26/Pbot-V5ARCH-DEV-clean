import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.trade_entry import (
    _calculate_adverse_slippage_pct,
    _evaluate_risk_reward_filter,
    _fetch_exchange_position_amount,
    _resolve_average_fill_price,
)
from core.trade_manager import execute_order


class ExecuteOrderCoverageTest(unittest.TestCase):
    """Covers execute_order code paths not yet tested by test_advanced_runtime_flows."""

    def test_adverse_slippage_is_directional(self):
        self.assertAlmostEqual(_calculate_adverse_slippage_pct("BUY", 100.0, 100.2), 0.2)
        self.assertEqual(_calculate_adverse_slippage_pct("BUY", 100.0, 99.8), 0.0)
        self.assertAlmostEqual(_calculate_adverse_slippage_pct("SELL", 100.0, 99.8), 0.2)
        self.assertEqual(_calculate_adverse_slippage_pct("SELL", 100.0, 100.2), 0.0)

    def test_adverse_slippage_handles_invalid_requested_price(self):
        self.assertEqual(_calculate_adverse_slippage_pct("BUY", 0.0, 100.0), 0.0)

    def test_boolean_exchange_numbers_are_rejected(self):
        self.assertIsNone(_resolve_average_fill_price({"average": True}, 1.0))

    def test_average_fill_price_uses_cost_when_average_is_missing(self):
        order = {"cost": 200.4, "price": 101.0}

        self.assertAlmostEqual(_resolve_average_fill_price(order, 2.0), 100.2)

    def test_average_fill_price_ignores_malformed_optional_fields(self):
        order = {"average": "invalid", "cost": "invalid", "price": 100.5}

        self.assertIsNone(_resolve_average_fill_price(order, 2.0))

    def test_average_fill_price_rejects_non_finite_and_non_positive_values(self):
        for invalid_average in (float("nan"), float("inf"), 0.0, -1.0):
            with self.subTest(invalid_average=invalid_average):
                order = {"average": invalid_average, "cost": 200.4, "price": 101.0}
                self.assertAlmostEqual(_resolve_average_fill_price(order, 2.0), 100.2)

    def test_average_fill_price_returns_none_when_all_sources_are_invalid(self):
        order = {"average": float("nan"), "cost": 0.0, "price": -1.0}

        self.assertIsNone(_resolve_average_fill_price(order, 2.0))

    def test_position_snapshot_rejects_malformed_or_contradictory_data(self):
        for positions in ({}, "bad", [None]):
            with self.subTest(positions=positions):
                bot = SimpleNamespace(execution=SimpleNamespace(fetch_positions=lambda: positions))
                self.assertIsNone(_fetch_exchange_position_amount(bot, "BTC/USDT", "BUY"))

        bot = SimpleNamespace(
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "side": "long",
                        "contracts": 0.5,
                        "info": {"positionAmt": "-0.5"},
                    }
                ]
            )
        )
        self.assertIsNone(_fetch_exchange_position_amount(bot, "BTC/USDT", "BUY"))

        bot = SimpleNamespace(
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "side": "long",
                        "contracts": 0.0,
                        "info": {"positionAmt": "0.5"},
                    }
                ]
            )
        )
        self.assertIsNone(_fetch_exchange_position_amount(bot, "BTC/USDT", "BUY"))

    def test_position_snapshot_uses_signed_exchange_amount_when_side_is_missing(self):
        bot = SimpleNamespace(
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.75,
                        "info": {"positionAmt": "-0.75"},
                    }
                ]
            )
        )

        self.assertEqual(_fetch_exchange_position_amount(bot, "BTC/USDT", "SELL"), 0.75)
        self.assertEqual(_fetch_exchange_position_amount(bot, "BTC/USDT", "BUY"), 0.0)

    def test_position_snapshot_rejects_quantity_mismatch_when_side_is_missing(self):
        bot = SimpleNamespace(
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 0.75,
                        "info": {"positionAmt": "0.25"},
                    }
                ]
            )
        )

        self.assertIsNone(_fetch_exchange_position_amount(bot, "BTC/USDT", "BUY"))

    def test_position_snapshot_accepts_unsigned_contracts_with_explicit_short_side(self):
        bot = SimpleNamespace(
            execution=SimpleNamespace(
                fetch_positions=lambda: [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "side": "short",
                        "contracts": 0.5,
                        "info": {},
                    }
                ]
            )
        )

        self.assertEqual(_fetch_exchange_position_amount(bot, "BTC/USDT", "SELL"), 0.5)

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.0)
    def test_risk_reward_filter_allows_valid_buy(self):
        ok, details = _evaluate_risk_reward_filter(
            side="BUY",
            entry_price=100.0,
            sl_val=98.0,
            tp_val=104.0,
            spread=0.0,
            atr_pct=0.01,
        )

        self.assertTrue(ok)
        self.assertAlmostEqual(details["actual_rrr"], 2.0)

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.0)
    def test_risk_reward_filter_blocks_invalid_buy(self):
        ok, details = _evaluate_risk_reward_filter(
            side="BUY",
            entry_price=100.0,
            sl_val=98.0,
            tp_val=102.0,
            spread=0.0,
            atr_pct=0.01,
        )

        self.assertFalse(ok)
        self.assertEqual(details["reason"], "RISK_REWARD_VETO")

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.0)
    def test_risk_reward_filter_allows_valid_sell(self):
        ok, details = _evaluate_risk_reward_filter(
            side="SELL",
            entry_price=100.0,
            sl_val=102.0,
            tp_val=96.0,
            spread=0.0,
            atr_pct=0.01,
        )

        self.assertTrue(ok)
        self.assertAlmostEqual(details["actual_rrr"], 2.0)

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.0)
    def test_risk_reward_filter_blocks_invalid_sell(self):
        ok, details = _evaluate_risk_reward_filter(
            side="SELL",
            entry_price=100.0,
            sl_val=102.0,
            tp_val=98.0,
            spread=0.0,
            atr_pct=0.01,
        )

        self.assertFalse(ok)
        self.assertEqual(details["reason"], "RISK_REWARD_VETO")

    def test_risk_reward_filter_blocks_invalid_bounds(self):
        ok, details = _evaluate_risk_reward_filter(
            side="BUY",
            entry_price=0.0,
            sl_val=98.0,
            tp_val=104.0,
            spread=0.0,
            atr_pct=0.01,
        )

        self.assertFalse(ok)
        self.assertEqual(details["reason"], "INVALID_BOUNDS")

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.001)
    def test_risk_reward_filter_spread_penalty_reduces_rrr(self):
        _ok_clean, clean = _evaluate_risk_reward_filter(
            side="BUY",
            entry_price=100.0,
            sl_val=98.0,
            tp_val=104.0,
            spread=0.0,
            atr_pct=0.01,
        )
        _ok_penalized, penalized = _evaluate_risk_reward_filter(
            side="BUY",
            entry_price=100.0,
            sl_val=98.0,
            tp_val=104.0,
            spread=0.004,
            atr_pct=0.01,
        )

        self.assertLess(penalized["actual_rrr"], clean["actual_rrr"])

    def _min_bot(self, **overrides):
        attrs = dict(
            log=MagicMock(),
            lock=RLock(),
            db_lock=RLock(),
            balance=5000.0,
            available_balance=5000.0,
            is_paused=False,
            circuit_breaker_active=False,
            integrity_lock_active=False,
            halt_system_active=False,
            instance_uuid="test-inst-uuid",
            ghost_model=object(),
            last_entry_open_ts=0.0,
            last_shadow_signal_ts=0.0,
            _symbol_reduced_size_mult=1.0,
            market_btc_change_tf=0.0,
            cooldown_pairs={},
            active_trades={},
            _load_runtime_symbol_controls=lambda: {"blocked": set(), "reduced": set()},
            _get_base_coin=lambda s: s.split("/")[0],
            get_current_balance=lambda: 5000.0,
            ws_manager=SimpleNamespace(get_l2_state=lambda _s: {}),
            brain=SimpleNamespace(
                get_genetic_params=lambda _s: {},
                get_stats_by_trend=lambda: {},
                save_active_trade_state=MagicMock(return_value=True),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
                log_signal_alert=MagicMock(),
                update_signal_alert_status=MagicMock(),
            ),
            data_service=SimpleNamespace(sanitize_context=lambda ctx: ctx or {}),
            risk_engine=SimpleNamespace(
                calculate_position_size=lambda **kw: (1.0, 100.0),
                get_exit_levels=lambda **kw: (99.0, 120.0, "STD"),
                check_market_safety=lambda *a, **kw: (True, "OK", 80),
            ),
            execution=SimpleNamespace(
                exchange=object(),
                fetch_ticker=lambda _s: {"last": 100.0},
                set_leverage=MagicMock(),
                place_hard_sl=MagicMock(
                    side_effect=lambda symbol, side, amount, stop_price, **kw: {
                        "id": "sl-1",
                        "symbol": symbol,
                        "type": "STOP_MARKET",
                        "side": "sell" if str(side).lower() == "buy" else "buy",
                        "amount": amount,
                        "status": "open",
                        "info": {"reduceOnly": True},
                    }
                ),
            ),
        )
        attrs.update(overrides)
        return SimpleNamespace(**attrs)

    def _ctx(self, **overrides):
        ctx = {
            "atr_pct": 0.01,
            "trend": "RANGO",
            "spread": 0.0,
            "prob_final": 75.0,
        }
        ctx.update(overrides)
        return ctx

    # --- Early-exit guard paths ---

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_shadow_cooldown_rejects_recent_signal(self, _):
        bot = self._min_bot()
        bot.last_shadow_signal_ts = 9999999999.0
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )
        self.assertTrue(result.startswith("SHADOW_COOLDOWN"))

    @patch("core.trade_entry.Config.REQUIRE_GHOST_MODEL_FOR_TRADING", True)
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_ghost_model_missing_rejects(self, _):
        bot = self._min_bot(ghost_model=None)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "GHOST_MODEL_MISSING")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_bot_paused_rejects(self, _):
        bot = self._min_bot(is_paused=True)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "BOT_PAUSED")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_circuit_breaker_active_rejects(self, _):
        bot = self._min_bot(circuit_breaker_active=True)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "CIRCUIT_BREAKER_PANIC")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_global_cooldown_rejects(self, _):
        bot = self._min_bot(last_entry_open_ts=9999999999.0)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "GLOBAL_COOLDOWN")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_already_active_rejects_for_closed_status(self, _):
        bot = self._min_bot()
        bot.active_trades["ETH/USDT"] = {
            "symbol": "ETH/USDT",
            "is_shadow": True,
            "side": "BUY",
            "status": "CLOSED",
            "sector": "OTHE",
        }
        result = execute_order(
            bot, "ETH/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "ALREADY_ACTIVE")

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_duplicate_real_coin_rejected(self, _):
        bot = self._min_bot()
        bot._get_base_coin = lambda s: "BTC" if "BTC" in s else s.split("/")[0]
        bot.active_trades["BTCBULL/USDT"] = {
            "is_shadow": False,
            "side": "BUY",
            "status": "OPEN",
            "sector": "OTHE",
        }
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "DUPLICATE_REAL_COIN")

    # --- Degradation paths ---

    @patch("core.trade_entry.Config.NATR_THRESHOLD", 1.0)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_high_volatility_degrades_to_shadow(self, _, _tg):
        bot = self._min_bot()
        ctx = self._ctx(atr_pct=0.02)
        result = execute_order(bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=ctx)
        self.assertEqual(result, "OK_DEGRADED: HIGH_VOLATILITY")

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_market_safety_degrades_to_shadow(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.check_market_safety = lambda *a, **kw: (False, "HIGH_RISK", 30)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "OK_DEGRADED: HIGH_RISK")

    @patch("core.trade_entry.Config.MAX_DIRECTIONAL_TRADES", 1)
    @patch("core.trade_entry.Config.MAX_SHADOW_TRADES", 1)
    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_max_directional_degrades_when_shadow_available(self, _, _tg):
        bot = self._min_bot()
        bot.active_trades.update(
            {
                "ETH/USDT": {
                    "symbol": "ETH/USDT",
                    "side": "BUY",
                    "is_shadow": False,
                    "status": "OPEN",
                    "sector": "OTHE",
                },
            }
        )
        ctx = self._ctx()
        result = execute_order(bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=ctx)
        self.assertEqual(result, "OK_DEGRADED: MAX_DIRECTIONAL_DEGRADED")

    @patch("core.trade_entry.Config.MAX_DIRECTIONAL_TRADES", 1)
    @patch("core.trade_entry.Config.MAX_SHADOW_TRADES", 1)
    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_max_directional_blocks_when_shadow_also_full(self, _, _tg):
        bot = self._min_bot()
        bot.active_trades.update(
            {
                "ETH/USDT": {
                    "symbol": "ETH/USDT",
                    "side": "BUY",
                    "is_shadow": False,
                    "status": "OPEN",
                    "sector": "OTHE",
                },
                "SOL/USDT": {
                    "symbol": "SOL/USDT",
                    "side": "BUY",
                    "is_shadow": True,
                    "status": "OPEN",
                    "sector": "OTHE",
                },
            }
        )
        ctx = self._ctx()
        result = execute_order(bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=ctx)
        self.assertEqual(result, "MAX_DIRECTIONAL")

    # --- Execution failure paths ---

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_size_error_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.calculate_position_size = lambda **kw: (0.0, 0.0)
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "SIZE_ERROR")

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_execution_no_fill_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_positions = MagicMock(return_value=[])
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 0.0, "average": None}
        )
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "EXECUTION_NO_FILL")
        bot.execution.fetch_positions.assert_called_once_with()
        self.assertFalse(bot.halt_system_active)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_zero_fill_with_exchange_exposure_protects_and_halts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_positions = MagicMock(
            return_value=[{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.75}]
        )
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 0.0, "average": 100.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_RECONCILED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertEqual(bot.execution.place_hard_sl.call_args.args[2], 0.75)
        self.assertTrue(bot.halt_system_active)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_open_zero_fill_with_flat_position_remains_unknown_and_halts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_positions = MagicMock(return_value=[])
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "open", "filled": 0.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_UNVERIFIED")
        bot.execution.place_hard_sl.assert_not_called()
        self.assertTrue(bot.halt_system_active)
        self.assertIsNone(bot.active_trades["BTC/USDT"]["amount"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_terminal_zero_fill_with_malformed_positions_halts(self, _, _tg):
        for positions in ({}, "bad", [None]):
            with self.subTest(positions=positions):
                bot = self._min_bot()
                bot.execution.fetch_positions = MagicMock(return_value=positions)
                bot.execution.create_precision_order = MagicMock(
                    return_value={"id": "o1", "status": "closed", "filled": 0.0}
                )

                result = execute_order(
                    bot,
                    "BTC/USDT",
                    "BUY",
                    100.0,
                    1.0,
                    is_shadow=False,
                    context=self._ctx(),
                )

                self.assertEqual(result, "ENTRY_FILL_AMOUNT_UNVERIFIED")
                self.assertTrue(bot.halt_system_active)
                self.assertIsNone(bot.active_trades["BTC/USDT"]["amount"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_canceled_partial_fill_is_protected_then_halted(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "canceled", "filled": 0.4, "average": 100.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_ORDER_STATUS_RECONCILIATION_REQUIRED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertEqual(bot.execution.place_hard_sl.call_args.args[2], 0.4)
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state["amount"], 0.4)
        self.assertTrue(state["entry_order_status_requires_reconciliation"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_positive_fill_without_exchange_order_id_is_protected_then_halted(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"status": "closed", "filled": 1.0, "average": 100.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_ORDER_ID_UNVERIFIED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertIsNone(state["entry_exchange_order_id"])
        self.assertTrue(state["entry_order_id_requires_reconciliation"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_missing_ack_reconciles_before_recording_unknown_amount(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_positions = MagicMock(return_value=[])
        bot.execution.create_precision_order = MagicMock(return_value=None)

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_UNVERIFIED")
        bot.execution.fetch_positions.assert_called_once_with()
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertIsNone(state["amount"])
        self.assertEqual(state["requested_amount"], 1.0)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_non_finite_filled_amount_halts_before_hard_sl(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": float("nan")}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_UNVERIFIED")
        bot.execution.place_hard_sl.assert_not_called()
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state["status"], "ENTRY_ACK_UNKNOWN")
        self.assertIsNone(state["amount"])
        self.assertEqual(state["requested_amount"], 1.0)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_missing_filled_amount_halts_before_hard_sl(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "open", "average": 100.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_UNVERIFIED")
        bot.execution.place_hard_sl.assert_not_called()
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state["status"], "ENTRY_ACK_UNKNOWN")
        self.assertIsNone(state["amount"])
        self.assertEqual(state["requested_amount"], 1.0)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_missing_filled_uses_exchange_position_then_protects_and_halts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_positions = MagicMock(
            return_value=[{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.75}]
        )
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "open", "average": 100.0}
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_RECONCILED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertEqual(bot.execution.place_hard_sl.call_args.args[2], 0.75)
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state["status"], "ENTRY_FILLED_AWAITING_POSITION_SYNC")
        self.assertTrue(state["fill_amount_reconciled_from_position"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_overfill_is_protected_then_halted_for_reconciliation(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={
                "id": "o1",
                "status": "closed",
                "filled": 2.0,
                "average": 100.0,
            }
        )

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_AMOUNT_MISMATCH")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertEqual(bot.execution.place_hard_sl.call_args.args[2], 2.0)
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state["status"], "ENTRY_FILLED_AWAITING_POSITION_SYNC")
        self.assertTrue(state["fill_amount_mismatch"])

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_leverage_setup_failure_aborts_before_order(self, _, _tg):
        bot = self._min_bot()
        bot.execution.set_leverage = MagicMock(return_value=None)
        bot.execution.create_precision_order = MagicMock()

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "LEVERAGE_SETUP_FAILED")
        bot.execution.create_precision_order.assert_not_called()

    @patch("core.trade_entry.Config.MAX_ENTRY_SL_PCT", 1.0)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_final_sl_too_wide_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.get_exit_levels = lambda **kw: (95.0, 120.0, "STD")

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )

        self.assertEqual(result, "FINAL_SL_TOO_WIDE")

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.0)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_risk_reward_veto_aborts_before_pending_intent(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.get_exit_levels = lambda **kw: (98.0, 102.0, "STD")

        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )

        self.assertEqual(result, "RISK_REWARD_VETO")
        bot.brain.save_active_trade_state.assert_not_called()

    @patch("core.trade_entry.Config.MIN_NOTIONAL_VALUE", 50.0)
    @patch("core.trade_entry.Config.CORRELATION_RISK_ENABLED", True)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_post_reduction_min_notional_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.calculate_position_size = lambda **kw: (1.0, 100.0)
        with patch(
            "core.trade_entry.compute_correlation_reduction",
            return_value=(0.4, [{"correlation": 1.0}]),
        ):
            result = execute_order(
                bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
            )

        self.assertEqual(result, "POST_REDUCTION_MIN_NOTIONAL")

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_hard_sl_fail_triggers_failsafe(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        bot.execution.place_hard_sl = MagicMock(return_value=None)
        bot.execution.last_hard_sl_error = "insufficient balance"
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "ENTRY_ABORTED_NO_HARD_SL")

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_hard_sl_invalid_ack_triggers_failsafe(self, _, _tg):
        """ACK truthy pero inválido (side opuesto) debe tratarse igual que
        un fallo del SL: fail-safe close para no dejar la posición desnuda."""
        from tests.test_bot_guardian_hard_sl import _valid_hard_sl_ack

        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        # Trade BUY requiere SL 'sell'; el exchange devuelve un ACK con side 'buy'
        # (respuesta de tipo equivocado o takeover silencioso). NO debe aceptarse.
        bad_ack = _valid_hard_sl_ack("BTC/USDT", "buy", 1.0)
        bot.execution.place_hard_sl = MagicMock(return_value=bad_ack)
        bot.execution.last_hard_sl_error = ""

        import core.trade_entry as te_mod

        called_fail_safe = {"count": 0}

        def _spy_fail_safe(*a, **kw):
            called_fail_safe["count"] += 1
            return True

        orig = te_mod._fail_safe_close_when_sl_missing
        te_mod._fail_safe_close_when_sl_missing = _spy_fail_safe
        try:
            result = execute_order(
                bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
            )
            self.assertEqual(result, "ENTRY_ABORTED_NO_HARD_SL")
            self.assertEqual(called_fail_safe["count"], 1)
        finally:
            te_mod._fail_safe_close_when_sl_missing = orig

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_hard_sl_fail_with_failsafe_failure_halts_system(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        bot.execution.place_hard_sl = MagicMock(return_value=None)
        bot.execution.last_hard_sl_error = "API error"

        import core.trade_helpers as th

        orig = th._fail_safe_close_when_sl_missing
        th._fail_safe_close_when_sl_missing = lambda *a, **kw: False
        try:
            result = execute_order(
                bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
            )
            self.assertEqual(result, "ENTRY_ABORTED_NO_HARD_SL")
            self.assertTrue(bot.is_paused)
            self.assertTrue(bot.integrity_lock_active)
            self.assertTrue(bot.halt_system_active)
        finally:
            th._fail_safe_close_when_sl_missing = orig

    @patch("core.trade_entry.Config.RISK_REWARD_FILTER_ENABLED", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_tp_insufficient_for_real_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.risk_engine.get_exit_levels = lambda **kw: (99.9, 100.05, "STD")
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "TP_INSUFFICIENT")

    # --- High spread veto ---

    @patch("core.trade_entry.Config.ENTRY_SPREAD_VETO_THRESHOLD", 0.0001)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_high_spread_veto_aborts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.fetch_book_ticker = MagicMock(
            return_value={"bidPrice": "99.0", "askPrice": "101.0"}
        )
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        result = execute_order(
            bot, "BTC/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertTrue(result.startswith("HIGH_SPREAD_VETO"))

    # --- Success paths ---

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_refreshed_price_is_used_before_exit_levels_and_sizing(self, _, _tg):
        bot = self._min_bot()
        seen = {}

        def _exit_levels(**kw):
            seen["exit_entry_price"] = kw["entry_price"]
            return 118.0, 130.0, "STD"

        def _sizing(**kw):
            seen["sizing_price"] = kw["price"]
            return 1.0, kw["price"]

        bot.execution.fetch_ticker = MagicMock(return_value={"last": 120.0})
        bot.risk_engine.get_exit_levels = _exit_levels
        bot.risk_engine.calculate_position_size = _sizing

        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )

        self.assertEqual(result, "OK")
        self.assertEqual(seen["exit_entry_price"], 120.0)
        self.assertEqual(seen["sizing_price"], 120.0)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_real_entry_success(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "real-o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "OK")
        self.assertIn("SOL/USDT", bot.active_trades)
        self.assertFalse(bot.active_trades["SOL/USDT"].get("is_shadow"))

    @patch("core.trade_entry.Config.MAX_SLIPPAGE", 0.001)
    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.append_execution_event")
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_real_entry_emits_adverse_slippage_breach(self, _, _tg, mocked_event):
        bot = self._min_bot()
        call_order = []

        def _record_event(_bot, event, _payload):
            call_order.append(event)

        original_place_hard_sl = bot.execution.place_hard_sl

        def _record_hard_sl(*args, **kwargs):
            call_order.append("PLACE_HARD_SL")
            return original_place_hard_sl(*args, **kwargs)

        mocked_event.side_effect = _record_event
        bot.execution.place_hard_sl = MagicMock(side_effect=_record_hard_sl)
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "real-o1", "status": "closed", "filled": 1.0, "cost": 100.2}
        )

        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "OK")
        breach_events = [
            call for call in mocked_event.call_args_list if call.args[1] == "ENTRY_SLIPPAGE_BREACH"
        ]
        self.assertEqual(len(breach_events), 1)
        self.assertAlmostEqual(breach_events[0].args[2]["adverse_slippage_pct"], 0.2)
        self.assertLess(
            call_order.index("PLACE_HARD_SL"), call_order.index("ENTRY_SLIPPAGE_BREACH")
        )
        self.assertLess(
            call_order.index("ORDER_PROTECTION_ATTACHED"),
            call_order.index("ENTRY_SLIPPAGE_BREACH"),
        )

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.append_execution_event")
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_real_fill_without_verified_price_protects_then_halts(self, _, _tg, mocked_event):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={
                "id": "real-o1",
                "status": "closed",
                "filled": 1.0,
                "average": float("nan"),
                "cost": 0.0,
                "price": -1.0,
            }
        )

        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_PRICE_UNVERIFIED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        state = bot.active_trades["SOL/USDT"]
        self.assertIsNone(state["entry"])
        self.assertTrue(state["entry_price_unverified"])
        self.assertEqual(state["status"], "ENTRY_FILLED_AWAITING_POSITION_SYNC")
        events = [call.args[1] for call in mocked_event.call_args_list]
        self.assertIn("ORDER_PROTECTION_ATTACHED", events)
        self.assertIn("ENTRY_FILL_PRICE_UNVERIFIED_HALT", events)
        self.assertNotIn("ENTRY_SLIPPAGE_BREACH", events)

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.append_execution_event")
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_unverified_fill_state_persistence_failure_remains_halted(self, _, _tg, mocked_event):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "real-o1", "status": "closed", "filled": 1.0, "price": 100.1}
        )
        save_calls = [True, False]
        bot.brain.save_active_trade_state = MagicMock(side_effect=save_calls)

        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "ENTRY_FILL_PRICE_STATE_PERSIST_FAILED")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        events = [call.args[1] for call in mocked_event.call_args_list]
        self.assertIn("ORDER_PROTECTION_ATTACHED", events)
        self.assertIn("ENTRY_FILL_PRICE_STATE_PERSIST_FAILED_HALT", events)
        self.assertNotIn("ENTRY_SLIPPAGE_BREACH", events)

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_shadow_entry_success(self, _, _tg):
        bot = self._min_bot()
        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )
        self.assertEqual(result, "OK")
        self.assertIn("SOL/USDT", bot.active_trades)
        self.assertTrue(bot.active_trades["SOL/USDT"].get("is_shadow"))

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_override_usd_size_controls_notional_before_final_guards(self, _, _tg):
        bot = self._min_bot()

        result = execute_order(
            bot,
            "SOL/USDT",
            "BUY",
            100.0,
            1.0,
            is_shadow=True,
            context=self._ctx(),
            override_usd_size=250.0,
        )

        self.assertEqual(result, "OK")
        self.assertAlmostEqual(bot.active_trades["SOL/USDT"].get("size_usd"), 250.0)
        self.assertAlmostEqual(bot.active_trades["SOL/USDT"].get("amount"), 2.5)

    @patch("core.trade_entry.Config.PAPER_MODE", True)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_paper_entry_success(self, _, _tg):
        bot = self._min_bot()
        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "OK")
        self.assertIn("SOL/USDT", bot.active_trades)
        self.assertTrue(bot.active_trades["SOL/USDT"].get("simulated_real"))

    # --- Persistence guard ---

    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_persistence_guard_triggers_on_final_db_failure(self, _tg, _mock_tg):
        bot = self._min_bot()
        call_count = [0]

        def _save_side_effect(symbol, state):
            call_count[0] += 1
            return call_count[0] < 2

        bot.brain.save_active_trade_state = MagicMock(side_effect=_save_side_effect)
        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=True, context=self._ctx()
        )
        self.assertEqual(result, "PERSISTENCE_GUARD_ACTIVE")

    @patch("core.trade_entry.Config.PAPER_MODE", False)
    @patch("core.trade_entry.send_telegram_msg")
    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_real_final_persistence_failure_halts(self, _, _tg):
        bot = self._min_bot()
        bot.execution.create_precision_order = MagicMock(
            return_value={"id": "real-o1", "status": "closed", "filled": 1.0, "average": 100.0}
        )
        bot.brain.save_active_trade_state = MagicMock(side_effect=[True, False])

        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )

        self.assertEqual(result, "PERSISTENCE_GUARD_ACTIVE")
        bot.execution.place_hard_sl.assert_called_once()
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)

    # --- Trade limit checks ---

    @patch("core.trade_entry.shadow_logger.is_trading_halted", return_value=False)
    def test_max_real_trades_rejected(self, _):
        bot = self._min_bot()
        for i in range(10):
            bot.active_trades[f"COIN{i:03d}/USDT"] = {
                "symbol": f"COIN{i:03d}/USDT",
                "is_shadow": False,
                "side": "BUY",
                "status": "OPEN",
                "sector": "OTHE",
            }
        result = execute_order(
            bot, "SOL/USDT", "BUY", 100.0, 1.0, is_shadow=False, context=self._ctx()
        )
        self.assertEqual(result, "MAX_REAL_TRADES")


if __name__ == "__main__":
    unittest.main()

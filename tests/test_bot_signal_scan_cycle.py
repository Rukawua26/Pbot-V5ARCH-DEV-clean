import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from core.bot_signals import run_signal_scan_cycle


class BotSignalScanCycleTest(unittest.TestCase):
    def _signal_stats(self):
        return {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "REAL": 0, "SHADOW": 0, "VETO": 0}

    def _valid_results(self, symbol="BTC/USDT", elapsed=100):
        frame = pd.DataFrame({"close": [100.0, 101.0]})
        return {symbol: {"data": (frame, frame), "elapsed": elapsed, "error": None}}

    def _scan_bot(self, **overrides):
        attrs = dict(
            lock=RLock(),
            active_trades={},
            cooldown_pairs={},
            cooldown_deadlines_mono={},
            latency_quarantine={},
            market_breadth={},
            log=MagicMock(),
            update_radar=MagicMock(),
            _load_runtime_symbol_controls=lambda: {"blocked": set(), "reduced": set()},
            _analyze_symbol_candidate=MagicMock(),
        )
        attrs.update(overrides)
        return SimpleNamespace(**attrs)

    def test_timeout_result_does_not_trigger_latency_quarantine(self):
        bot = SimpleNamespace(
            latency_quarantine={},
            market_breadth={},
            log=MagicMock(),
            update_radar=MagicMock(),
        )
        top_triage = [{"symbol": "BTC/USDT"}]
        results = {"BTC/USDT": {"data": None, "elapsed": -1, "error": "TIMEOUT"}}
        signal_stats = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "REAL": 0, "SHADOW": 0, "VETO": 0}

        run_signal_scan_cycle(bot, top_triage, results, signal_stats, pnl_real_hoy=0.0)

        self.assertEqual(bot.latency_quarantine, {})
        self.assertFalse(
            any("VETO LATENCIA" in str(call.args[0]) for call in bot.log.call_args_list)
        )
        bot.update_radar.assert_called_once()

    def test_cheap_prefilter_blocks_symbol_before_analysis(self):
        bot = self._scan_bot(
            _load_runtime_symbol_controls=lambda: {"blocked": {"BTC"}, "reduced": set()}
        )

        run_signal_scan_cycle(
            bot,
            [{"symbol": "BTC/USDT"}],
            self._valid_results(),
            self._signal_stats(),
            pnl_real_hoy=0.0,
        )

        bot._analyze_symbol_candidate.assert_not_called()
        self.assertEqual(bot.update_radar.call_args.args[5]["filter_reason"], "SYMBOL_BLOCKED")

    def test_cheap_prefilter_skips_active_symbol_before_analysis(self):
        bot = self._scan_bot(active_trades={"BTC/USDT": {"symbol": "BTC/USDT", "status": "OPEN"}})

        run_signal_scan_cycle(
            bot,
            [{"symbol": "BTC/USDT"}],
            self._valid_results(),
            self._signal_stats(),
            pnl_real_hoy=0.0,
        )

        bot._analyze_symbol_candidate.assert_not_called()
        self.assertIn("OPERACIÓN ACTIVA", bot.update_radar.call_args.args[4])

    def test_cheap_prefilter_quarantines_latency_before_analysis(self):
        bot = self._scan_bot()

        run_signal_scan_cycle(
            bot,
            [{"symbol": "BTC/USDT"}],
            self._valid_results(elapsed=999999),
            self._signal_stats(),
            pnl_real_hoy=0.0,
        )

        bot._analyze_symbol_candidate.assert_not_called()
        self.assertIn("BTC/USDT", bot.latency_quarantine)

    def test_no_data_result_does_not_trigger_latency_quarantine(self):
        bot = SimpleNamespace(
            latency_quarantine={},
            market_breadth={},
            log=MagicMock(),
            update_radar=MagicMock(),
        )
        top_triage = [{"symbol": "ETH/USDT"}]
        results = {"ETH/USDT": {"data": (None, None), "elapsed": 120, "error": None}}
        signal_stats = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "REAL": 0, "SHADOW": 0, "VETO": 0}

        run_signal_scan_cycle(bot, top_triage, results, signal_stats, pnl_real_hoy=0.0)

        self.assertEqual(bot.latency_quarantine, {})
        self.assertFalse(
            any("VETO LATENCIA" in str(call.args[0]) for call in bot.log.call_args_list)
        )
        bot.update_radar.assert_called_once()

    def test_triary_spread_is_propagated_to_execution_context(self):
        captured = {}

        def build_context(symbol_raw, symbol, df_main, price, ind, audit_signal):
            captured["ind_spread"] = ind.get("spread")
            ctx = {"spread": ind.get("spread", 0.0), "tier": "IRON"}
            return {"signal": audit_signal, "mode": "SHADOW"}, ctx, "⚪", 1.0

        bot = self._scan_bot(
            _analyze_symbol_candidate=MagicMock(
                return_value=("BUY", "SHADOW", 100.0, 80.0, {"rsi": {"val": 55}}, {})
            ),
            _build_symbol_context=MagicMock(side_effect=build_context),
            _apply_entry_filters_and_adjust_prob=MagicMock(
                return_value=(80.0, True, "Filter Pass", {"spread": 0.0015, "tier": "IRON"})
            ),
            _resolve_audit_verdict_and_stats=MagicMock(return_value="🧪 SHADOW"),
            _update_signal_diagnostics=MagicMock(),
            _plan_execution_mode=MagicMock(
                return_value=(True, True, "🧪 SHADOW", True, "Filter Pass")
            ),
            _execute_and_update_symbol=MagicMock(),
            scanner_history=[],
            scanner_lock=None,
            bootstrap_heuristic_mode=True,
            last_ml_confidence=0.0,
            last_ghost_weight=0.0,
        )

        run_signal_scan_cycle(
            bot,
            [{"symbol": "BTC/USDT", "spread": 0.0015}],
            self._valid_results(),
            self._signal_stats(),
            pnl_real_hoy=0.0,
        )

        self.assertEqual(captured["ind_spread"], 0.0015)
        bot._execute_and_update_symbol.assert_called_once()
        self.assertEqual(bot._execute_and_update_symbol.call_args.kwargs["ctx"]["spread"], 0.0015)


if __name__ == "__main__":
    unittest.main()

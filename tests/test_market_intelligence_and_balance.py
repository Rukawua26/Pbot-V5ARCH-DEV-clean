import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from core import bot_balance_ops, bot_cycles, bot_main_loop, bot_market_state, market_intelligence
from core.strategy.regime_hmm import DynamicHMMRegime


def _ticker(symbol, *, volume=50_000_000, price=1.0, percentage=1.0):
    return {
        "symbol": symbol,
        "quoteVolume": float(volume),
        "last": float(price),
        "percentage": float(percentage),
    }


def _build_market_bot(tickers, *, fetch_tickers_error=None):
    snapshot = [
        {
            "symbol": symbol,
            "symbol_raw": symbol,
            "ticker": ticker,
            "vol_24h": float(ticker.get("quoteVolume", 0) or 0),
            "status": "ACTIVE",
        }
        for symbol, ticker in sorted(
            tickers.items(),
            key=lambda item: item[1].get("quoteVolume", 0),
            reverse=True,
        )
        if "/USDT" in symbol
    ]
    execution = SimpleNamespace(
        fetch_tickers=MagicMock(side_effect=fetch_tickers_error)
        if fetch_tickers_error
        else MagicMock(return_value=tickers),
        fetch_ticker=MagicMock(side_effect=RuntimeError("fallback unavailable")),
    )
    brain = SimpleNamespace(
        get_symbol_performance=MagicMock(
            side_effect=lambda sym: (
                {"wr": 85, "trades": 10} if sym.startswith("ALPHA") else {"wr": 45, "trades": 10}
            )
        ),
        get_symbol_blacklist=MagicMock(return_value=[]),
    )
    return SimpleNamespace(
        execution=execution,
        brain=brain,
        data_service=SimpleNamespace(audit_symbol_maturity=MagicMock(return_value=True)),
        risk_engine=SimpleNamespace(
            check_anti_revenge_blacklist=MagicMock(return_value=(True, ""))
        ),
        blacklist={},
        restricted_sectors=set(),
        pairs_to_scan=[],
        scanner_history=[],
        market_btc_price=0.0,
        lock=threading.RLock(),
        log=MagicMock(),
        _load_runtime_symbol_controls=MagicMock(
            return_value={"blocked": set(), "preferred": set()}
        ),
        _get_active_market_snapshot=MagicMock(return_value=snapshot),
    )


class MarketIntelligencePipelineTests(unittest.TestCase):
    @patch.object(market_intelligence.Config, "MAX_REAL_PAIRS", 10)
    @patch.object(market_intelligence.Config, "MAX_SHADOW_PAIRS", 10)
    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    @patch.object(market_intelligence.Config, "MIN_VOLUME_24H", 15_000_000)
    @patch.object(market_intelligence.Config, "PRICE_PRIORITY_LIMIT", 3.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_VOL_LOW_PRICE", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_WR", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_OTHERS", 1.0)
    def test_acquire_targets_prioritizes_high_wr_liquid_symbols(self):
        tickers = {
            "ALPHA/USDT": _ticker("ALPHA/USDT", volume=90_000_000, price=1.0),
            "BETA/USDT": _ticker("BETA/USDT", volume=85_000_000, price=1.0),
            "LOWVOL/USDT": _ticker("LOWVOL/USDT", volume=1_000_000, price=1.0),
            "BTC/USDT": _ticker("BTC/USDT", volume=100_000_000, price=65_000.0),
        }
        bot = _build_market_bot(tickers)

        result = market_intelligence.acquire_targets(bot)

        self.assertEqual(result["ALPHA/USDT"], tickers["ALPHA/USDT"])
        self.assertIn("ALPHA/USDT", bot.pairs_to_scan)
        self.assertNotIn("LOWVOL/USDT", bot.pairs_to_scan)
        self.assertLess(
            bot.pairs_to_scan.index("ALPHA/USDT"),
            bot.pairs_to_scan.index("BETA/USDT"),
        )
        self.assertEqual(bot.market_btc_price, 65_000.0)
        self.assertTrue(any(item["symbol"] == "ALPHA/USDT" for item in bot.scanner_history))
        bot.execution.fetch_tickers.assert_not_called()

    @patch.object(market_intelligence.Config, "MAX_REAL_PAIRS", 10)
    @patch.object(market_intelligence.Config, "MAX_SHADOW_PAIRS", 10)
    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 1)
    @patch.object(market_intelligence.Config, "MIN_VOLUME_24H", 15_000_000)
    @patch.object(market_intelligence.Config, "PRICE_PRIORITY_LIMIT", 3.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_VOL_LOW_PRICE", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_WR", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_OTHERS", 1.0)
    def test_acquire_targets_filters_extreme_pump_symbols(self):
        tickers = {
            "PUMPED/USDT": _ticker("PUMPED/USDT", volume=80_000_000, percentage=50.0),
            "NORMAL/USDT": _ticker("NORMAL/USDT", volume=80_000_000, percentage=5.0),
            "BTC/USDT": _ticker("BTC/USDT", volume=100_000_000, price=65_000.0),
        }
        bot = _build_market_bot(tickers)

        market_intelligence.acquire_targets(bot)

        self.assertNotIn("PUMPED/USDT", bot.pairs_to_scan)
        self.assertIn("NORMAL/USDT", bot.pairs_to_scan)

    def test_acquire_targets_returns_empty_when_ticker_fetch_fails(self):
        bot = _build_market_bot({}, fetch_tickers_error=RuntimeError("API down"))
        bot.pairs_to_scan = []

        result = market_intelligence.acquire_targets(bot)

        self.assertEqual(result, {})
        bot._get_active_market_snapshot.assert_called_once()
        bot.execution.fetch_ticker.assert_called_once_with("BTC/USDT")

    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    @patch.object(market_intelligence.Config, "TRIAGE_RVOL_EMA_ALPHA", 0.5)
    def test_get_active_market_snapshot_builds_ranked_pairs_from_stream_snapshot(self):
        tickers = {
            "ALPHA/USDT": {
                **_ticker("ALPHA/USDT", volume=80_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
            "BETA/USDT": {
                **_ticker("BETA/USDT", volume=60_000_000, price=2.0),
                "bid": 1.999,
                "ask": 2.0,
            },
            "BULL/USDT": {
                **_ticker("BULL/USDT", volume=90_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
            "LOWVOL/USDT": {
                **_ticker("LOWVOL/USDT", volume=1_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(
                return_value=[
                    {"symbol": "ALPHAUSDT", "bidPrice": "0.999", "askPrice": "1.0"},
                    {"symbol": "BETAUSDT", "bidPrice": "1.999", "askPrice": "2.0"},
                ]
            ),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(execution=execution, weight_tracker=None, log=MagicMock())

        ranked = market_intelligence.get_active_market_snapshot(bot)

        symbols = [item["symbol"] for item in ranked]
        self.assertIn("ALPHA/USDT", symbols)
        self.assertIn("BETA/USDT", symbols)
        self.assertNotIn("BULL/USDT", symbols)
        self.assertIn("LOWVOL/USDT", symbols)
        execution.load_markets.assert_not_called()

    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    def test_get_active_market_snapshot_caps_dynamic_pair_list_to_top_triage_count(self):
        tickers = {
            f"SYM{i}/USDT": {
                **_ticker(f"SYM{i}/USDT", volume=90_000_000 - i, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            }
            for i in range(5)
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(return_value=[]),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(execution=execution, weight_tracker=None, log=MagicMock())

        ranked = market_intelligence.get_active_market_snapshot(bot)

        self.assertEqual(len(ranked), 2)

    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    def test_get_active_market_snapshot_uses_pool_limit_for_candidate_pool(self):
        tickers = {
            f"SYM{i}/USDT": {
                **_ticker(f"SYM{i}/USDT", volume=90_000_000 - i, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            }
            for i in range(5)
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(return_value=[]),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(execution=execution, weight_tracker=None, log=MagicMock())

        ranked = market_intelligence.get_active_market_snapshot(bot, pool_limit=4)

        self.assertEqual(len(ranked), 4)

    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 5)
    @patch.object(market_intelligence.Config, "BEAR_TREND_MAX_PAIRS", 5)
    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    def test_get_active_market_snapshot_preserves_liquidity_order_in_bear_trend(self):
        cached_candidates = [
            {
                "symbol": "LOW/USDT",
                "ticker": {
                    **_ticker("LOW/USDT", volume=20_000_000, price=1.0),
                    "bid": 0.999,
                    "ask": 1.0,
                },
                "vol_24h": 20_000_000,
                "last": 1.0,
            },
            {
                "symbol": "HIGH/USDT",
                "ticker": {
                    **_ticker("HIGH/USDT", volume=80_000_000, price=1.0),
                    "bid": 0.999,
                    "ask": 1.0,
                },
                "vol_24h": 80_000_000,
                "last": 1.0,
            },
        ]
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(
                return_value=[
                    {"symbol": "LOWUSDT", "bidPrice": "0.999", "askPrice": "1.0"},
                    {"symbol": "HIGHUSDT", "bidPrice": "0.999", "askPrice": "1.0"},
                ]
            ),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(),
        )
        bot = SimpleNamespace(
            execution=execution,
            weight_tracker=None,
            market_regime="BEAR_TREND",
            _market_cache={"candidates": cached_candidates},
            _market_cache_ts=time.time(),
            log=MagicMock(),
        )

        ranked = market_intelligence.get_active_market_snapshot(bot)

        self.assertEqual([item["symbol"] for item in ranked], ["HIGH/USDT", "LOW/USDT"])
        execution.fetch_tickers.assert_not_called()

    @patch.object(bot_cycles.Config, "TOP_TRIAGE_COUNT", 2)
    def test_run_triage_cycle_exposes_only_top_triage_symbols_to_pairs_to_scan(self):
        snapshot = [
            {"symbol": "A/USDT", "ticker": {}},
            {"symbol": "B/USDT", "ticker": {}},
            {"symbol": "C/USDT", "ticker": {}},
        ]
        bot = SimpleNamespace(
            _get_active_market_snapshot=MagicMock(return_value=snapshot),
            _snapshot_tickers={},
            pairs_to_scan=[],
            brain=SimpleNamespace(
                get_symbol_performance=MagicMock(return_value={"wr": 50, "trades": 0}),
                get_symbol_blacklist=MagicMock(return_value=[]),
            ),
            data_service=SimpleNamespace(audit_symbol_maturity=MagicMock(return_value=True)),
            risk_engine=SimpleNamespace(
                check_anti_revenge_blacklist=MagicMock(return_value=(True, ""))
            ),
            restricted_sectors=set(),
            _load_runtime_symbol_controls=MagicMock(
                return_value={"blocked": set(), "preferred": set()}
            ),
            market_regime="UNKNOWN",
            log=MagicMock(),
        )

        triage_snapshot, _ = bot_cycles.run_triage_cycle(bot)

        self.assertEqual(triage_snapshot, snapshot[:2])
        self.assertEqual(bot.pairs_to_scan, ["A/USDT", "B/USDT"])

    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    @patch.object(market_intelligence.Config, "TRIAGE_CANDIDATE_POOL_MULTIPLIER", 2)
    def test_run_triage_cycle_filters_operability_before_final_cut(self):
        snapshot = [
            {"symbol": "A/USDT", "ticker": _ticker("A/USDT", volume=90_000_000)},
            {"symbol": "B/USDT", "ticker": _ticker("B/USDT", volume=80_000_000)},
            {"symbol": "C/USDT", "ticker": _ticker("C/USDT", volume=70_000_000)},
        ]
        bot = SimpleNamespace(
            _get_active_market_snapshot=MagicMock(return_value=snapshot),
            _snapshot_tickers={},
            pairs_to_scan=[],
            brain=SimpleNamespace(
                get_symbol_performance=MagicMock(return_value={"wr": 50, "trades": 0}),
                get_symbol_blacklist=MagicMock(return_value=[]),
            ),
            data_service=SimpleNamespace(audit_symbol_maturity=MagicMock(return_value=True)),
            risk_engine=SimpleNamespace(
                check_anti_revenge_blacklist=MagicMock(return_value=(True, ""))
            ),
            restricted_sectors=set(),
            _load_runtime_symbol_controls=MagicMock(
                return_value={"blocked": {"A"}, "preferred": set()}
            ),
            market_regime="UNKNOWN",
            weight_tracker=None,
            log=MagicMock(),
        )

        triage_snapshot, _ = bot_cycles.run_triage_cycle(bot)

        self.assertEqual([item["symbol"] for item in triage_snapshot], ["B/USDT", "C/USDT"])
        self.assertEqual(bot.pairs_to_scan, ["B/USDT", "C/USDT"])
        bot._get_active_market_snapshot.assert_called_once_with(pool_limit=4)

    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    def test_hard_operability_filter_skips_symbol_errors(self):
        snapshot = [
            {"symbol": "BROKEN/USDT", "ticker": _ticker("BROKEN/USDT", volume=90_000_000)},
            {"symbol": "HEALTHY/USDT", "ticker": _ticker("HEALTHY/USDT", volume=80_000_000)},
        ]
        bot = SimpleNamespace(
            pairs_to_scan=[],
            brain=SimpleNamespace(
                get_symbol_performance=MagicMock(return_value={"wr": 50, "trades": 0}),
                get_symbol_blacklist=MagicMock(return_value=[]),
            ),
            data_service=SimpleNamespace(
                audit_symbol_maturity=MagicMock(side_effect=[RuntimeError("maturity down"), True])
            ),
            risk_engine=SimpleNamespace(
                check_anti_revenge_blacklist=MagicMock(return_value=(True, ""))
            ),
            restricted_sectors=set(),
            _load_runtime_symbol_controls=MagicMock(
                return_value={"blocked": set(), "preferred": set()}
            ),
            market_regime="UNKNOWN",
            log=MagicMock(),
        )

        targets = market_intelligence.build_operable_targets(bot, snapshot)

        self.assertEqual([item["symbol"] for item in targets], ["HEALTHY/USDT"])
        bot.log.assert_any_call("⚠️ Error en filtros duros para BROKEN/USDT: maturity down")

    @patch.object(bot_main_loop.Config, "BREAKOUT_WATCH_ENABLED", False)
    @patch.object(bot_main_loop.Config, "ML_HEALTH_VETO_ENABLED", False)
    def test_main_loop_does_not_reacquire_after_empty_triage_targets(self):
        bot = SimpleNamespace(
            is_running=True,
            init_complete=SimpleNamespace(wait=MagicMock()),
            ws_manager=None,
            _guardian_loop=MagicMock(),
            _refresh_symbol_controls_if_due=MagicMock(),
            _run_crash_predictor_cycle=MagicMock(return_value=False),
            check_weekly_schedule=MagicMock(),
            check_weekly_maintenance_utc=MagicMock(),
            daily_initial_balance=1000.0,
            balance=1000.0,
            brain=SimpleNamespace(get_daily_real_pnl=MagicMock(return_value=(0.0, 0.0))),
            check_safety_and_goals=MagicMock(),
            last_radar_update=0,
            _run_market_refresh_cycle=MagicMock(),
            _run_triage_cycle=MagicMock(return_value=([], {"BTC/USDT": {"last": 65000.0}})),
            last_pm_check=time.time(),
            _perform_post_mortem=MagicMock(),
            _run_periodic_housekeeping=MagicMock(side_effect=lambda now, a, b, c: (a, b, c)),
            _run_btc_panic_cycle=MagicMock(),
            ml_healthy=True,
            pairs_to_scan=[],
            acquire_targets=MagicMock(),
            _run_cycle_wait_and_api_log=MagicMock(),
            log=MagicMock(),
        )

        def stop_after_wait():
            bot.is_running = False

        bot._run_cycle_wait_and_api_log.side_effect = stop_after_wait

        bot_main_loop.run_main_logic(bot)

        bot.acquire_targets.assert_not_called()
        bot._run_cycle_wait_and_api_log.assert_called_once()

    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    def test_get_active_market_snapshot_removes_stale_or_wide_spread_pairs(self):
        tickers = {
            "KEEP/USDT": {
                **_ticker("KEEP/USDT", volume=50_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
                "info": {"raw": "large-exchange-payload"},
            },
            "WIDE/USDT": {
                **_ticker("WIDE/USDT", volume=50_000_000, price=1.0),
                "bid": 0.90,
                "ask": 1.0,
            },
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(return_value=[]),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(
            execution=execution,
            weight_tracker=None,
            _market_cache={},
            _market_cache_ts=0,
            log=MagicMock(),
        )

        ranked = market_intelligence.get_active_market_snapshot(bot)

        symbols = [item["symbol"] for item in ranked]
        self.assertIn("KEEP/USDT", symbols)
        self.assertNotIn("WIDE/USDT", symbols)
        self.assertNotIn("MISSING/USDT", symbols)
        keep_ticker = next(item["ticker"] for item in ranked if item["symbol"] == "KEEP/USDT")
        self.assertEqual(keep_ticker["last"], 1.0)
        self.assertEqual(keep_ticker["quoteVolume"], 50_000_000.0)
        self.assertNotIn("info", keep_ticker)


class BalanceOpsPressureTests(unittest.TestCase):
    def test_get_current_balance_returns_cached_balance_on_api_failure(self):
        bot = SimpleNamespace(
            execution=SimpleNamespace(get_balance=MagicMock(side_effect=RuntimeError("API Error"))),
            available_balance=100.0,
            log=MagicMock(),
        )

        self.assertEqual(bot_balance_ops.get_current_balance(bot), 100.0)
        bot.log.assert_called_once()

    @patch.object(bot_balance_ops.Config, "PAPER_MODE", True)
    @patch.object(bot_balance_ops.Config, "PAPER_INITIAL_BALANCE", 1_000.0)
    def test_start_silent_sync_uses_paper_initial_balance_when_zeroed(self):
        bot = SimpleNamespace(
            is_running=True,
            balance=0.0,
            available_balance=0.0,
            daily_initial_balance=0.0,
            lock=threading.RLock(),
            log=MagicMock(),
        )

        def _stop_after_first_sleep(_seconds):
            bot.is_running = False

        with patch.object(bot_balance_ops.time, "sleep", side_effect=_stop_after_first_sleep):
            bot_balance_ops.start_silent_sync(bot)

        self.assertEqual(bot.balance, 1_000.0)
        self.assertEqual(bot.available_balance, 1_000.0)
        self.assertEqual(bot.daily_initial_balance, 1_000.0)


class RegimePipelineTests(unittest.TestCase):
    def test_hmm_predicts_range_with_deterministic_model_state(self):
        np.random.seed(42)
        close = pd.Series(100.0 + np.sin(np.arange(240) / 8.0) * 0.2)
        df_sideways = pd.DataFrame({"close": close})
        hmm = DynamicHMMRegime(lookback_candles=200)
        hmm._fit_features(df_sideways)
        hmm.model = SimpleNamespace(
            predict_proba=MagicMock(return_value=np.array([[0.1, 0.8, 0.1]]))
        )
        hmm.state_map = {0: "BEAR_TREND", 1: "RANGE", 2: "BULL_TREND"}
        hmm.is_ready = True

        regime, confidence = hmm.predict_regime(df_sideways)

        self.assertEqual(regime, "RANGE")
        self.assertEqual(confidence, 0.8)

    def test_hmm_returns_unknown_when_not_ready_with_insufficient_data(self):
        hmm = DynamicHMMRegime(lookback_candles=200)
        short_df = pd.DataFrame({"close": [100.0] * 50})

        self.assertFalse(hmm.dynamic_retrain(short_df))
        self.assertEqual(hmm.predict_regime(short_df), ("UNKNOWN", 0.0))

    def test_heuristic_market_regime_returns_range_without_btc_price(self):
        bot = SimpleNamespace(
            market_btc_price=0,
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock()),
            log=MagicMock(),
        )

        result = bot_market_state._detect_market_regime_heuristic(bot, None)

        self.assertEqual(result, "RANGE")
        self.assertEqual(bot.market_regime_source, "HEURISTIC")
        bot.data_service.fetch_and_update_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pandas as pd


class TelemetryCoverageTest(unittest.TestCase):
    def test_collect_telemetry_success_and_error(self):
        from core.bot_telemetry import collect_telemetry

        bot = SimpleNamespace(
            db_lock=RLock(),
            balance=100.0,
            market_btc_price=50000.0,
            market_btc_price_source="WS",
            market_btc_price_ts=0.0,
            market_regime="BULL",
            market_regime_source="HMM",
            market_regime_confidence=0.7,
            btc_panic=False,
            fear_greed=55,
            circuit_breaker_active=False,
            risk_multiplier=1.1,
            data_service=SimpleNamespace(data_cache={"BTC": object()}),
            brain=SimpleNamespace(
                get_ai_maturity=MagicMock(return_value={"xp_percent": 42, "rank": "SILVER"}),
                get_stats=MagicMock(return_value={"shadow_win_rate": 60.0, "real_win_rate": 55.0, "total_trades": 3, "shadow_trades": 2}),
                get_daily_real_pnl=MagicMock(return_value=(1.2, 12.0)),
            ),
        )
        logger = SimpleNamespace(error=MagicMock())

        stats = collect_telemetry(bot, logger)
        self.assertEqual(stats["rank"], "SILVER")
        self.assertEqual(stats["cached_pairs"], 1)

        bot.brain.get_ai_maturity.side_effect = RuntimeError("db")
        fallback = collect_telemetry(bot, logger)
        self.assertEqual(fallback["rank"], "ERROR")
        self.assertEqual(fallback["balance"], 100.0)


class CommandCoverageTest(unittest.TestCase):
    def _bot(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [[4], [3], [1.25]]
        conn.cursor.return_value = cursor
        trades = [
            {"symbol": "BTC", "pnl_percent": 2.0, "is_shadow": False},
            {"symbol": "ETH", "pnl_percent": -1.0, "is_shadow": True},
            {"symbol": "BTC", "pnl_percent": 3.0, "is_shadow": True},
        ]
        return SimpleNamespace(
            db_lock=RLock(),
            balance=100.0,
            dynamic_offset=0.05,
            last_signal_stats={"BUY": 2, "SELL": 1, "NEUTRAL": 1, "REAL": 1, "SHADOW": 2, "VETO": 1},
            breakout_agent=SimpleNamespace(
                size=MagicMock(return_value=3),
                summary_by_source=MagicMock(return_value={"SHOCK_VETO": 1, "COHERENCE_VETO": 2}),
                watchlist={
                    "BTC": {"symbol": "BTC", "side": "BUY", "ia_prob": 91.0, "updated_at": 1, "meta": {"source": "TEST", "shock_dist_pct": 0.5}}
                },
            ),
            pairs_to_scan=["BTC/USDT", "ETH/USDT"],
            active_trades={"BTC": {"side": "BUY", "pnl": 1.0}},
            lock=RLock(),
            scanner_history=[{"symbol": "BTC", "ia_prob": "95%"}, {"symbol": "ETH", "ia_prob": "70%"}],
            brain=SimpleNamespace(
                get_last_n_trades=MagicMock(return_value=trades),
                get_ai_maturity=MagicMock(return_value={"rank": "GOLD", "xp_percent": 75}),
                get_daily_real_pnl=MagicMock(return_value=(1.0, 10.0)),
                _get_conn=MagicMock(return_value=conn),
                get_recent_vetos=MagicMock(return_value=[{"symbol": "BTC", "reason": "RISK", "context_summary": "ctx"}]),
                check_consecutive_losses=MagicMock(side_effect=lambda symbol, _n: symbol.startswith("ETH")),
                get_agent_reputation=MagicMock(return_value={"MT": 101.0, "G": 88.0}),
                get_model_insights=MagicMock(return_value={"top_features": [("rsi", 0.2)], "learned_rule": "rule"}),
            ),
            weight_tracker=SimpleNamespace(get_formatted_report=MagicMock(return_value="ok")),
        )

    @patch("core.commands.api_status.send_telegram_msg")
    def test_api_status_commands(self, send_msg):
        from core.commands.api_status import _handle_api_status_commands

        bot = self._bot()
        self.assertTrue(_handle_api_status_commands(bot, "/api"))
        self.assertIn("ok", send_msg.call_args.args[0])
        bot.weight_tracker = None
        self.assertTrue(_handle_api_status_commands(bot, "/weight"))
        self.assertFalse(_handle_api_status_commands(bot, "/nope"))

    @patch("core.commands.audit.platform.python_version", return_value="3.x")
    @patch("core.commands.audit.send_telegram_msg")
    def test_audit_status_signals_shadow_and_report_commands(self, send_msg, _pyver):
        from core.commands.audit import _handle_audit_commands

        bot = self._bot()
        with patch("tools.reporter.generate_audit_report", return_value="AUDIT"):
            self.assertTrue(_handle_audit_commands(bot, "/audit"))
        self.assertTrue(_handle_audit_commands(bot, "/audit_report"))
        self.assertTrue(_handle_audit_commands(bot, "/status"))
        self.assertTrue(_handle_audit_commands(bot, "/signals"))
        self.assertTrue(_handle_audit_commands(bot, "/shadow_stats"))
        self.assertFalse(_handle_audit_commands(bot, "/nope"))
        self.assertGreaterEqual(send_msg.call_count, 6)

    @patch("core.commands.intelligence.send_telegram_msg")
    def test_intelligence_commands_cover_common_paths(self, send_msg):
        from core.commands.intelligence import _handle_intelligence_commands

        bot = self._bot()
        with patch("tools.reporter.generate_mobile_report", return_value="REPORT"):
            for command in (
                "/thinking",
                "/watchlist",
                "/quarantine",
                "/agents",
                "/intelligence",
                "/report",
                "/open",
                "/top",
                "/targets",
            ):
                self.assertTrue(_handle_intelligence_commands(bot, command))
        self.assertFalse(_handle_intelligence_commands(bot, "/nope"))
        self.assertGreaterEqual(send_msg.call_count, 9)


class DataServiceCoverageTest(unittest.TestCase):
    def _service(self):
        from core.data_service import DataService

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch("core.data_service._BASE_DIR", tmp.name):
            service = DataService(SimpleNamespace())
        service.cache_dir = str(Path(tmp.name) / "candles")
        service.maturity_file = str(Path(tmp.name) / "maturity.json")
        os.makedirs(service.cache_dir, exist_ok=True)
        self.addCleanup(lambda: service.shutdown(wait=False))
        return service

    def test_cache_snapshot_save_maturity_and_sanitize(self):
        service = self._service()
        df = pd.DataFrame({"time": [2, 1, 1], "open": [1, 1, 1], "high": [2, 2, 2], "low": [0.5, 0.5, 0.5], "close": [1.5, 1.5, 1.5], "volume": [10, 10, 10]})
        clean = service._clean_df(df)
        self.assertEqual(list(clean["time"]), [1, 2])
        service.data_cache["BTC/USDT_1h"] = df
        snapshot = service._snapshot_cache_for_save()
        self.assertIn("BTC/USDT_1h", snapshot)
        service._write_cache_snapshot(snapshot)
        self.assertTrue(os.listdir(service.cache_dir))
        service.maturity_cache = {"BTC": True}
        service.save_maturity_cache()
        self.assertTrue(Path(service.maturity_file).exists())
        service.save_maturity_cache()
        context = {"df": df, "x": 1}
        self.assertEqual(service.sanitize_context(context), {"x": 1})
        service.shutdown(wait=False)

    def test_async_cache_and_maturity_paths(self):
        service = self._service()
        self.assertFalse(service.save_cache_async())
        service.data_cache["BTC_1h"] = pd.DataFrame({"time": [1], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]})
        self.assertTrue(service.save_cache_async())
        self.assertFalse(service.save_cache_async())
        service.maturity_cache = {"BTC": True}
        self.assertTrue(service.save_maturity_cache_async(force=True))
        self.assertFalse(service.save_maturity_cache_async(force=True))
        service.shutdown(wait=True)

    def test_audit_symbol_maturity_and_fetch_update_data(self):
        service = self._service()
        rows = [[i, 1, 2, 0.5, 1.5, 10] for i in range(210)]
        service.exchange = SimpleNamespace(
            fetch_ohlcv=MagicMock(return_value=rows),
            parse_timeframe=MagicMock(return_value=3600),
        )
        tracker = SimpleNamespace(track=MagicMock())
        service.set_weight_tracker(tracker)
        self.assertTrue(service.audit_symbol_maturity("BTC/USDT"))
        self.assertTrue(service.maturity_cache["BTC/USDT"])
        with patch("core.data_service.Config.MIN_CANDLE_HISTORY", 2), patch("core.data_service.Config.CANDLE_FETCH_LIMIT", 100):
            out = service.fetch_and_update_data("BTC/USDT", "1h")
        self.assertIsNotNone(out)
        self.assertIn("BTC/USDT_1h", service.data_cache)
        tracker.track.assert_called()
        service.shutdown(wait=False)

    def test_download_multiscale_uses_download_historical(self):
        service = self._service()
        service.download_historical_data = MagicMock(return_value=pd.DataFrame({"time": [1]}))
        result = service.download_multiscale_historical_data("BTC/USDT", days=1, timeframes=["15m", "1h"])
        self.assertEqual(set(result), {"15m", "1h"})
        self.assertEqual(service.download_historical_data.call_count, 2)
        service.shutdown(wait=False)

    def test_download_historical_data_paginates_and_stores(self):
        service = self._service()
        now_ms = 1_000_000_000
        batch1 = [[now_ms - 3_600_000, 1, 2, 0.5, 1.5, 10]]
        batch2 = [[now_ms - 1_800_000, 1, 2, 0.5, 1.5, 11]]
        service.exchange = SimpleNamespace(
            parse_timeframe=MagicMock(return_value=1800),
            fetch_ohlcv=MagicMock(side_effect=[batch1, batch2]),
        )
        with patch("core.data_service.time.time", return_value=now_ms / 1000), patch(
            "core.data_service.time.sleep"
        ), patch("pandas.DataFrame.to_parquet") as to_parquet:
            df = service.download_historical_data("BTC/USDT", "30m", days=1, limit_per_call=1)
        self.assertEqual(len(df), 2)
        self.assertIn("BTC/USDT_30m", service.data_cache)
        self.assertEqual(service.exchange.fetch_ohlcv.call_count, 2)
        to_parquet.assert_called_once()
        service.shutdown(wait=False)


class BotCycleAndSignalCoverageTest(unittest.TestCase):
    def test_btc_indicator_helpers_and_triage(self):
        from core.bot_cycles import _btc_cache_marker, _resolve_triage_worker_count, prepare_top_triage, run_market_refresh_cycle, run_triage_cycle

        self.assertIsNone(_btc_cache_marker(pd.DataFrame()))
        self.assertEqual(_btc_cache_marker(pd.DataFrame({"time": [1, 2]})), 2)
        self.assertGreaterEqual(_resolve_triage_worker_count(5), 1)
        bot = SimpleNamespace(
            last_market_update=0,
            log=MagicMock(),
            acquire_targets=MagicMock(),
            _get_active_market_snapshot=MagicMock(return_value=[{"symbol": "BTC/USDT", "ticker": {"last": 1}}]),
            _snapshot_tickers={},
            pairs_to_scan=[],
            latency_quarantine={},
            ws_manager=SimpleNamespace(update_symbols=MagicMock()),
            _update_scanner_status=MagicMock(),
            update_radar=MagicMock(),
        )
        with patch("core.bot_cycles.time.time", return_value=50000.0):
            run_market_refresh_cycle(bot)
        bot.acquire_targets.assert_called_once()
        with patch("core.bot_cycles.build_operable_targets", return_value=[{"symbol": "BTC/USDT"}]), patch("core.bot_cycles.get_candidate_pool_limit", return_value=1):
            triage, tickers = run_triage_cycle(bot)
        self.assertEqual(triage[0]["symbol"], "BTC/USDT")
        self.assertIn("BTC/USDT", tickers)
        with patch("core.bot_cycles.Config.TOP_TRIAGE_COUNT", 1):
            top = prepare_top_triage(bot, [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}])
        self.assertEqual(top, [{"symbol": "BTC/USDT"}])

    @patch("core.bot_cycles.send_telegram_msg")
    def test_market_context_cycle_sets_sentiment_from_cached_btc_data(self, _send_msg):
        from core.bot_cycles import run_market_context_cycle

        rows = 210
        btc_1h = pd.DataFrame(
            {
                "close": [100.0] * rows,
                "high": [101.0] * rows,
                "low": [99.0] * rows,
                "EMA_200": [90.0] * rows,
                "ADX_14": [25.0] * rows,
            }
        )
        bot = SimpleNamespace(
            daily_initial_balance=100.0,
            balance=100.0,
            brain=SimpleNamespace(get_daily_real_pnl=MagicMock(return_value=(1.0, 10.0))),
            price_lock=RLock(),
            live_prices={},
            live_prices_ts={},
            market_btc_price=0.0,
            market_btc_price_source="UNKNOWN",
            market_btc_price_ts=0.0,
            _get_cached_btc_data=MagicMock(return_value=btc_1h),
            current_sentiment=("🔴 TENDENCIA BAJISTA", "red"),
            log=MagicMock(),
            execution=SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 100.0})),
        )

        pnl = run_market_context_cycle(bot, {"BTC/USDT": {"last": 100.0}})

        self.assertEqual(pnl, 1.0)
        self.assertEqual(bot.market_btc_price_source, "REST_TICKER")
        self.assertEqual(bot.current_sentiment[0], "🟢 TENDENCIA ALCISTA")

    def test_signal_precompute_and_scan_simple_paths(self):
        from core.bot_signals import _precompute_signal_analysis, run_signal_scan_cycle

        df = pd.DataFrame({"close": [1, 2]})
        bot = SimpleNamespace(
            log=MagicMock(),
            update_radar=MagicMock(),
            latency_quarantine={},
            market_regime="RANGE",
            market_regime_source="TEST",
            bootstrap_heuristic_mode=False,
            ghost_weight_override=35.0,
            lock=RLock(),
            db_lock=RLock(),
            active_trades={},
            scanner_history=[{"symbol": "BTC/USDT", "result": ""}],
            data_service=SimpleNamespace(sanitize_context=MagicMock(side_effect=lambda ctx: ctx)),
            brain=SimpleNamespace(log_signal_alert=MagicMock()),
            main_loop=None,
            _update_signal_diagnostics=MagicMock(),
            _build_symbol_context=MagicMock(return_value=({"signal": "BUY", "mode": "SHADOW"}, {}, "⚪", 1.0)),
            _apply_entry_filters_and_adjust_prob=MagicMock(return_value=(75.0, True, "OK", {})),
            _resolve_audit_verdict_and_stats=MagicMock(return_value="OK"),
            _plan_execution_mode=MagicMock(return_value=(False, True, "OK", True, "OK")),
            _execute_and_update_symbol=MagicMock(),
            _analyze_symbol_candidate=MagicMock(return_value=("BUY", "SHADOW", 10.0, 75.0, {}, {})),
        )
        top = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        results = {
            "BTC/USDT": {"data": (df, df), "elapsed": 10},
            "ETH/USDT": {"data": None, "elapsed": -1},
        }
        with patch("core.bot_signals.Config.SIGNAL_ANALYSIS_WORKERS", 1):
            self.assertEqual(_precompute_signal_analysis(bot, top, results), {})
        with patch("core.bot_signals.append_execution_event"), patch("core.bot_signals.calculate_market_breadth") as breadth:
            breadth.return_value = SimpleNamespace(as_dict=MagicMock(return_value={}), total_count=0)
            run_signal_scan_cycle(bot, top, results, {"x": 1}, 0.0)
        bot._analyze_symbol_candidate.assert_called_once()
        bot.update_radar.assert_called()


class OpsCommandCoverageTest(unittest.TestCase):
    def _bot(self):
        return SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            scanner_lock=RLock(),
            scanner_history=[{"symbol": "BTC", "ia_prob": "95%", "tier": "ELITE"}],
            market_btc_price_ts=0.0,
            market_regime="RANGE",
            market_regime_source="HMM",
            market_regime_confidence=0.5,
            hmm_markov_snapshot={"state": "RANGE", "bullish_breakout_prob": 10, "bearish_reversal_prob": 5, "range_prob": 85, "ts": datetime.now(UTC).isoformat()},
            markov_decision_stats={"range_breakout_allowed": 1, "range_standard_penalty": 2, "range_stagnant_veto": 3},
            market_btc_price=50000.0,
            market_btc_price_source="WS",
            weight_tracker=SimpleNamespace(get_status=MagicMock(return_value={"current_weight": 10, "limit": 2400, "usage_pct": 0.4})),
            main_loop=None,
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock(return_value=pd.DataFrame({"close": [1]}))),
            brain=SimpleNamespace(
                pending_model_update=False,
                rotate_history=MagicMock(return_value="backup.db"),
                delete_active_trade_state=MagicMock(),
            ),
            ghost_model=None,
            scaler=None,
            market_btc_change_tf=0.0,
            cooldown_pairs={"BTC": "x"},
            cooldown_deadlines_mono={"BTC": 1.0},
            risk_engine=SimpleNamespace(temp_blacklist={"ETH": 1}, symbol_streaks={"ETH": 2}),
            active_trades={"BTC/USDT": {"entry_client_order_id": "cid"}},
            execution=SimpleNamespace(
                fetch_open_orders=MagicMock(return_value=[]),
                fetch_order_by_client_id=MagicMock(return_value=None),
                fetch_positions=MagicMock(return_value=[]),
            ),
            current_sentiment=("RANGE", "yellow"),
        )

    @patch("core.commands.ops.send_telegram_msg")
    def test_misc_ops_commands(self, send_msg):
        from core.commands.ops import _handle_misc_commands

        bot = self._bot()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("logs").mkdir()
                now = datetime.now(UTC)
                rows = [
                    {"ts": now.isoformat(), "event": "ENTRY_ORDER_ACK"},
                    {"ts": now.isoformat(), "event": "INTENT_EXPIRED"},
                    {"bad": True},
                ]
                Path("logs/execution_events.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
                self.assertTrue(_handle_misc_commands(bot, "/pipeline"))
                self.assertTrue(_handle_misc_commands(bot, "/sre_intent"))
                self.assertTrue(_handle_misc_commands(bot, "/tiers"))
                self.assertTrue(_handle_misc_commands(bot, "/dump_db"))
                self.assertFalse(_handle_misc_commands(bot, "/nope"))
            finally:
                os.chdir(cwd)
        self.assertGreaterEqual(send_msg.call_count, 4)

    @patch("core.commands.ops.persist_cooldowns")
    @patch("core.commands.ops.send_telegram_msg")
    def test_training_maintenance_disabled_and_safe_commands(self, send_msg, persist):
        from core.commands.ops import _handle_training_and_maintenance_commands

        bot = self._bot()
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/force_train"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/evolution"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/genetic"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/force_shadow"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/archive"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/clean"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/unquarantine"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/thresholds"))
        self.assertFalse(_handle_training_and_maintenance_commands(bot, "/nope"))
        persist.assert_called_once()
        self.assertEqual(bot.cooldown_pairs, {})

    @patch("core.commands.ops.send_telegram_msg")
    def test_explain_and_force_clear_paths(self, send_msg):
        from core.commands.ops import _handle_training_and_maintenance_commands

        bot = self._bot()
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/explain"))
        with patch("tools.strategy.Strategy.analyze", return_value=("BUY", "SHADOW", 1.0, 70.0, {"rsi": {"val": 55.0}, "adx": {"val": 22.0}, "z_score": 1.0}, {"G": 60, "MT": 70, "SR": 80})):
            self.assertTrue(_handle_training_and_maintenance_commands(bot, "/explain BTC/USDT"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/force_clear"))
        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/force_clear BTC/USDT"))
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")


class BotIoLoopsCoverageTest(unittest.TestCase):
    def test_telegram_helpers_and_ticker_update(self):
        from core.bot_io_loops import (
            _apply_ticker_stream_update,
            _extract_telegram_message,
            _is_authorized_telegram_chat,
            _telegram_chat_id_hash,
            _telegram_command_name,
        )

        self.assertEqual(_extract_telegram_message({"edited_message": {"text": "x"}}), {"text": "x"})
        self.assertEqual(_telegram_command_name("/start now"), "/start")
        self.assertEqual(len(_telegram_chat_id_hash("123")), 12)
        with patch("core.bot_io_loops.Config.TELEGRAM_CHAT_ID", "123"), patch("core.bot_io_loops.Config.TELEGRAM_ADMIN_IDS", "7,8"):
            self.assertTrue(_is_authorized_telegram_chat("123", 7))
            self.assertFalse(_is_authorized_telegram_chat("123", 9))
            self.assertFalse(_is_authorized_telegram_chat("999", 7))

        bot = SimpleNamespace(
            price_lock=RLock(),
            live_prices={},
            live_prices_ts={},
            market_btc_price=0.0,
            market_btc_price_source="",
            market_btc_price_ts=0.0,
        )
        updated = _apply_ticker_stream_update(
            bot,
            [
                {"s": "BTCUSDT", "c": "50000"},
                {"s": "ETHUSDT", "c": "3000"},
                {"s": "BAD"},
            ],
        )
        self.assertEqual(updated, 2)
        self.assertEqual(bot.market_btc_price, 50000.0)

    def test_perform_post_mortem_updates_old_trades(self):
        from core.bot_io_loops import perform_post_mortem

        old_ts = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
        bot = SimpleNamespace(
            db_lock=RLock(),
            log=MagicMock(),
            execution=SimpleNamespace(fetch_ticker=MagicMock(return_value={"last": 90.0})),
            brain=SimpleNamespace(
                get_trades_pending_post_mortem=MagicMock(return_value=[{"id": 1, "timestamp": old_ts, "symbol": "BTC/USDT", "pnl_percent": -1.0, "side": "BUY", "exit_price": 100.0}]),
                update_post_mortem=MagicMock(),
            ),
        )

        perform_post_mortem(bot)

        bot.brain.update_post_mortem.assert_called_once()
        self.assertEqual(bot.brain.update_post_mortem.call_args.args[1]["verdict"], "FALSE_POSITIVE")

    @patch("core.bot_io_loops.time.sleep")
    @patch("core.bot_io_loops.append_execution_event")
    @patch("core.bot_io_loops.telegram_get_json")
    @patch("core.bot_io_loops.Config.TELEGRAM_TOKEN", "token")
    @patch("core.bot_io_loops.Config.TELEGRAM_CHAT_ID", "123")
    @patch("core.bot_io_loops.Config.TELEGRAM_ADMIN_IDS", "7")
    def test_telegram_listener_accepts_and_rejects_once(self, get_json, append_event, _sleep):
        from core.bot_io_loops import telegram_listener

        bot = SimpleNamespace(is_running=True, handle_command=MagicMock(), _telegram_last_err_log=0.0, log=MagicMock())
        get_json.return_value = {
            "result": [
                {"update_id": 1, "message": {"text": "/ok", "chat": {"id": "123"}, "from": {"id": 7}}},
                {"update_id": 2, "message": {"text": "/bad", "chat": {"id": "999"}, "from": {"id": 7}}},
                {"update_id": 3, "message": {"text": "", "chat": {"id": "123"}, "from": {"id": 7}}},
            ]
        }

        def stop_after_sleep(_seconds):
            bot.is_running = False

        _sleep.side_effect = stop_after_sleep
        telegram_listener(bot)

        bot.handle_command.assert_called_once_with("/ok")
        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("TELEGRAM_COMMAND_ACCEPTED", events)
        self.assertIn("TELEGRAM_COMMAND_REJECTED", events)


if __name__ == "__main__":
    unittest.main()

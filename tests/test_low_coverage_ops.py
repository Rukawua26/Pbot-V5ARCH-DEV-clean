import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch


class BotMaintenanceTest(unittest.TestCase):
    def test_backup_database_placeholder_copies_existing_files(self):
        from core.bot_maintenance import backup_database_placeholder

        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path("sniper_brain.db").write_text("db", encoding="utf-8")
                result = backup_database_placeholder()
            finally:
                os.chdir(cwd)

        self.assertIsNotNone(result)

    def test_backup_database_placeholder_returns_none_when_empty(self):
        from core.bot_maintenance import backup_database_placeholder

        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                result = backup_database_placeholder()
            finally:
                os.chdir(cwd)

        self.assertIsNone(result)

    def test_check_for_evolution_logs_when_due(self):
        from core.bot_maintenance import check_for_evolution

        cursor = MagicMock()
        cursor.fetchone.return_value = [5]
        conn = MagicMock(cursor=MagicMock(return_value=cursor))
        bot = SimpleNamespace(
            log=MagicMock(),
            brain=SimpleNamespace(
                get_last_train_timestamp=MagicMock(return_value=datetime.now() - timedelta(days=8)),
                _get_conn=MagicMock(return_value=conn),
            ),
        )

        check_for_evolution(bot)

        self.assertGreaterEqual(bot.log.call_count, 2)
        cursor.execute.assert_called_once()


class BotMiscOpsTest(unittest.TestCase):
    def test_load_ai_restrictions_logs_loaded_lists(self):
        from core.bot_misc_ops import load_ai_restrictions

        bot = SimpleNamespace(
            restricted_hours=[],
            restricted_sectors=[],
            log=MagicMock(),
            brain=SimpleNamespace(
                get_hourly_blacklist=MagicMock(return_value=["13"]),
                get_sector_blacklist=MagicMock(return_value=["AI"]),
            ),
        )

        load_ai_restrictions(bot)

        self.assertEqual(bot.restricted_hours, ["13"])
        self.assertEqual(bot.restricted_sectors, ["AI"])
        self.assertGreaterEqual(bot.log.call_count, 3)

    def test_load_ai_restrictions_logs_error(self):
        from core.bot_misc_ops import load_ai_restrictions

        bot = SimpleNamespace(
            log=MagicMock(),
            brain=SimpleNamespace(get_hourly_blacklist=MagicMock(side_effect=RuntimeError("db"))),
        )

        load_ai_restrictions(bot)

        bot.log.assert_called_once()

    def test_self_adjust_exigency_adjusts_for_low_shadow_wr(self):
        from core.bot_misc_ops import self_adjust_exigency

        bot = SimpleNamespace(
            db_lock=RLock(),
            dynamic_offset=0.0,
            brain=SimpleNamespace(get_stats=MagicMock(return_value={"shadow_win_rate": 40.0})),
        )

        suffix = self_adjust_exigency(bot)

        self.assertEqual(bot.dynamic_offset, 0.05)
        self.assertIn("EXIGENCIA", suffix)

    def test_get_vol_24h_matches_symbol_variants(self):
        from core.bot_misc_ops import get_vol_24h

        tickers = {"BTC/USDT": {"quoteVolume": "123"}, "ETH/USDT": {"quoteVolume": "456"}}

        self.assertEqual(get_vol_24h("BTC/USDT:USDT", tickers), 123.0)
        self.assertEqual(get_vol_24h("ETH/BUSD", tickers), 456.0)
        self.assertEqual(get_vol_24h("XRP/USDT", tickers), 0.0)

    def test_handle_command_export_paths(self):
        from core.bot_misc_ops import handle_command

        bot = SimpleNamespace()
        basic = MagicMock(return_value=False)
        export = MagicMock()
        notify = MagicMock()

        handle_command(bot, " /export_data ", basic, export, notify)
        export.assert_called_once()
        notify.assert_called_once_with("✅ Dataset Maestro exportado correctamente.")

        notify.reset_mock()
        handle_command(bot, "/export_data", basic, None, notify)
        notify.assert_called_once_with("❌ Script de exportación no encontrado.")


class BotRuntimeOpsTest(unittest.TestCase):
    def test_heartbeat_loop_sets_online_then_stops(self):
        from core.bot_runtime_ops import heartbeat_loop

        bot = SimpleNamespace(
            is_running=True, api_status="", log=MagicMock(), init_complete=Event()
        )
        bot.init_complete.set()

        def fetch_status():
            bot.is_running = False
            return {"status": "ok"}

        bot.execution = SimpleNamespace(exchange=SimpleNamespace(fetch_status=fetch_status))
        with (
            patch("core.bot_runtime_ops.Config.USE_TESTNET", False),
            patch("core.bot_runtime_ops.time.sleep"),
        ):
            heartbeat_loop(bot)

        self.assertEqual(bot.api_status, "🟢 ONLINE")

    def test_heartbeat_loop_uses_public_ticker_in_paper_testnet(self):
        from core.bot_runtime_ops import heartbeat_loop

        fetch_status = MagicMock()
        bot = SimpleNamespace(
            is_running=True,
            api_status="",
            log=MagicMock(),
            init_complete=MagicMock(),
        )

        def fetch_ticker(symbol):
            self.assertEqual(symbol, "BTC/USDT")
            bot.is_running = False
            return {"last": 1.0}

        bot.execution = SimpleNamespace(
            exchange=SimpleNamespace(fetch_status=fetch_status), fetch_ticker=fetch_ticker
        )
        with (
            patch("core.bot_runtime_ops.Config.PAPER_MODE", True),
            patch("core.bot_runtime_ops.Config.USE_TESTNET", True),
            patch("core.bot_runtime_ops.time.sleep"),
        ):
            heartbeat_loop(bot)

        bot.init_complete.wait.assert_called_once_with()
        fetch_status.assert_not_called()
        self.assertEqual(bot.api_status, "🟢 ONLINE")

    def test_heartbeat_loop_keeps_status_probe_in_real_testnet(self):
        from core.bot_runtime_ops import heartbeat_loop

        bot = SimpleNamespace(is_running=True, api_status="", log=MagicMock())

        def fetch_status():
            bot.is_running = False
            return {"status": "ok"}

        fetch_ticker = MagicMock()
        bot.execution = SimpleNamespace(
            exchange=SimpleNamespace(fetch_status=fetch_status), fetch_ticker=fetch_ticker
        )
        with (
            patch("core.bot_runtime_ops.Config.PAPER_MODE", False),
            patch("core.bot_runtime_ops.Config.USE_TESTNET", True),
            patch("core.bot_runtime_ops.time.sleep"),
        ):
            heartbeat_loop(bot)

        fetch_ticker.assert_not_called()
        self.assertEqual(bot.api_status, "🟢 ONLINE")

    def test_instinctive_safety_forces_shadow_on_high_atr(self):
        from core.bot_runtime_ops import check_instinctive_safety

        bot = SimpleNamespace(log=MagicMock())

        self.assertEqual(
            check_instinctive_safety(bot, "BTC/USDT", {"atr_pct": 0.06}), "FORCE_SHADOW"
        )
        self.assertEqual(check_instinctive_safety(bot, "BTC/USDT", {"atr_pct": 0.01}), "OK")

    def test_close_all_positions_emergency_closes_snapshot(self):
        from core.bot_runtime_ops import close_all_positions_emergency

        bot = SimpleNamespace(
            lock=RLock(),
            active_trades={"BTC/USDT": {"last_price": 100.0}, "ETH/USDT": {"last_price": 50.0}},
            close_trade=MagicMock(),
        )

        self.assertEqual(close_all_positions_emergency(bot), 2)
        self.assertEqual(bot.close_trade.call_count, 2)


class BotHousekeepingTest(unittest.TestCase):
    @staticmethod
    def _bot():
        return SimpleNamespace(
            balance=1000.0,
            log=MagicMock(),
            day_report_sent=False,
            daily_backup_done=False,
            last_ml_health_check=1_000_000.0,
            last_perf_check=1_000_000.0,
            _mobile_report_failure_count=0,
            _mobile_report_retry_after=0.0,
            _mobile_report_last_success=0.0,
            _mobile_report_last_error="",
        )

    def test_mobile_report_failure_is_contained_and_backed_off(self):
        from core.bot_housekeeping import _send_mobile_report

        bot = self._bot()
        with (
            patch("core.bot_housekeeping.time.time", return_value=100.0),
            patch(
                "tools.reporter.generate_mobile_report", side_effect=ModuleNotFoundError("reporter")
            ) as generate,
            patch("core.bot_housekeeping.append_runtime_metric") as metric,
        ):
            self.assertFalse(_send_mobile_report(bot))
            self.assertFalse(_send_mobile_report(bot))

        generate.assert_called_once_with(1000.0)
        self.assertEqual(bot._mobile_report_failure_count, 1)
        self.assertEqual(bot._mobile_report_retry_after, 160.0)
        self.assertIn("reporter", bot._mobile_report_last_error)
        metric.assert_called_once()
        self.assertFalse(metric.call_args.args[1]["ok"])

    def test_periodic_report_failure_does_not_escape_or_advance_timestamp(self):
        from core.bot_housekeeping import run_periodic_housekeeping

        bot = self._bot()
        now = datetime(2026, 7, 10, 12, 0)
        with (
            patch("core.bot_housekeeping.Config.AUTO_MOBILE_REPORTS_ENABLED", True),
            patch("core.bot_housekeeping.time.time", return_value=20_000.0),
            patch("tools.reporter.generate_mobile_report", side_effect=RuntimeError("generation")),
            patch("core.bot_housekeeping.append_runtime_metric"),
        ):
            result = run_periodic_housekeeping(bot, now, 0.0, 20_000.0, 20_000.0)

        self.assertEqual(result[0], 0.0)
        self.assertEqual(bot._mobile_report_failure_count, 1)

    def test_mobile_report_recovers_after_backoff(self):
        from core.bot_housekeeping import _send_mobile_report

        bot = self._bot()
        with (
            patch("core.bot_housekeeping.time.time", side_effect=[100.0, 161.0]),
            patch(
                "tools.reporter.generate_mobile_report",
                side_effect=[RuntimeError("generation"), "report"],
            ) as generate,
            patch("core.bot_housekeeping.send_telegram_msg", return_value=True) as send,
            patch("core.bot_housekeeping.append_runtime_metric"),
        ):
            self.assertFalse(_send_mobile_report(bot))
            self.assertTrue(_send_mobile_report(bot))

        self.assertEqual(generate.call_count, 2)
        send.assert_called_once_with("report")
        self.assertEqual(bot._mobile_report_failure_count, 0)
        self.assertEqual(bot._mobile_report_retry_after, 0.0)
        self.assertEqual(bot._mobile_report_last_success, 161.0)

    def test_periodic_reports_can_be_disabled(self):
        from core.bot_housekeeping import run_periodic_housekeeping

        bot = self._bot()
        with (
            patch("core.bot_housekeeping.Config.AUTO_MOBILE_REPORTS_ENABLED", False),
            patch("core.bot_housekeeping.time.time", return_value=20_000.0),
            patch("tools.reporter.generate_mobile_report") as generate,
        ):
            result = run_periodic_housekeeping(
                bot, datetime(2026, 7, 10, 12, 0), 0.0, 20_000.0, 20_000.0
            )

        generate.assert_not_called()
        self.assertEqual(result, (0.0, 20_000.0, 20_000.0))

    def test_daily_report_success_sets_state_and_timestamp(self):
        from core.bot_housekeeping import run_periodic_housekeeping

        bot = self._bot()
        with (
            patch("core.bot_housekeeping.Config.AUTO_MOBILE_REPORTS_ENABLED", True),
            patch("core.bot_housekeeping.time.time", return_value=30_000.0),
            patch("tools.reporter.generate_mobile_report", return_value="report"),
            patch("core.bot_housekeeping.send_telegram_msg", return_value=True) as send,
            patch("core.bot_housekeeping.append_runtime_metric"),
        ):
            result = run_periodic_housekeeping(
                bot, datetime(2026, 7, 10, 23, 0), 29_000.0, 30_000.0, 30_000.0
            )

        send.assert_called_once_with("📅 *REPORTE DE CIERRE DIARIO*\nreport")
        self.assertTrue(bot.day_report_sent)
        self.assertEqual(result[0], 30_000.0)
        self.assertEqual(bot._mobile_report_last_success, 30_000.0)


class SymbolControlsTest(unittest.TestCase):
    def test_load_runtime_symbol_controls_reads_file_and_caches(self):
        from core.bot_symbol_controls import load_runtime_symbol_controls

        payload = json.dumps(
            {
                "blocked_symbols": ["BTC/USDT"],
                "preferred_symbols": ["ETH/USDT"],
                "reduced_symbols": ["XRP/USDT"],
            }
        )
        bot = SimpleNamespace(_symbol_controls_cache=None, log=MagicMock())

        with (
            patch("core.bot_symbol_controls.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=payload)),
        ):
            controls = load_runtime_symbol_controls(bot)
            cached = load_runtime_symbol_controls(bot)

        self.assertEqual(controls, cached)
        self.assertEqual(controls["blocked"], {"BTC"})
        self.assertEqual(controls["preferred"], {"ETH"})
        self.assertEqual(controls["reduced"], {"XRP"})

    def test_refresh_symbol_controls_success_and_failure(self):
        from core.bot_symbol_controls import refresh_symbol_controls_if_due

        bot = SimpleNamespace(
            _symbol_controls_last_refresh=0.0,
            _symbol_controls_refresh_interval=60,
            _symbol_controls_cache={"loaded_at": 1.0},
            log=MagicMock(),
        )

        with (
            patch("core.bot_symbol_controls.time.time", return_value=1000.0),
            patch(
                "core.bot_symbol_controls.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stderr="", stdout=""),
            ),
        ):
            refresh_symbol_controls_if_due(bot)
        self.assertEqual(bot._symbol_controls_cache["loaded_at"], 0.0)

        bot._symbol_controls_last_refresh = 0.0
        with (
            patch("core.bot_symbol_controls.time.time", return_value=2000.0),
            patch(
                "core.bot_symbol_controls.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stderr="bad", stdout=""),
            ),
        ):
            refresh_symbol_controls_if_due(bot)
        self.assertIn("Error refreshing", bot.log.call_args.args[0])

    def test_funding_and_btc_cache_paths(self):
        from core.bot_symbol_controls import get_cached_btc_data, get_cached_funding_rate

        bot = SimpleNamespace(
            _funding_rate_cache={},
            _funding_cache_ttl=300,
            _btc_data_cache=None,
            _btc_data_cache_ts=0.0,
            execution=SimpleNamespace(
                fetch_funding_rate=MagicMock(return_value={"fundingRate": "0.001"})
            ),
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock(return_value={"bars": 1})),
        )

        with patch("core.bot_symbol_controls.time.time", return_value=100.0):
            self.assertEqual(get_cached_funding_rate(bot, "BTC/USDT"), 0.001)
            self.assertEqual(get_cached_btc_data(bot), {"bars": 1})
        with patch("core.bot_symbol_controls.time.time", return_value=120.0):
            self.assertEqual(get_cached_funding_rate(bot, "BTC/USDT"), 0.001)
            self.assertEqual(get_cached_btc_data(bot), {"bars": 1})
        bot.execution.fetch_funding_rate.assert_called_once()
        bot.data_service.fetch_and_update_data.assert_called_once()


class ProcessLockTest(unittest.TestCase):
    def test_acquire_single_instance_lock_success_and_busy(self):
        import core.process_lock as pl

        logger = SimpleNamespace(error=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = str(Path(tmp) / "lock")
            old_lock = pl._single_instance_lock
            try:
                with patch("core.process_lock._try_acquire_lock", return_value=True):
                    self.assertTrue(pl.acquire_single_instance_lock(logger, lock_path))
                pl._single_instance_lock.close()
                pl._single_instance_lock = None
                Path(lock_path).write_text("123", encoding="utf-8")
                with (
                    patch("core.process_lock._try_acquire_lock", return_value=False),
                    patch("sys.stderr"),
                ):
                    self.assertFalse(pl.acquire_single_instance_lock(logger, lock_path))
            finally:
                if getattr(pl, "_single_instance_lock", None) is not None:
                    pl._single_instance_lock.close()
                pl._single_instance_lock = old_lock


class MetricsExportTest(unittest.TestCase):
    def test_export_metrics_summary_writes_aggregates(self):
        from core.metrics_export import export_metrics_summary

        bot = SimpleNamespace(
            lock=RLock(),
            active_trades={
                "BTC/USDT": {"pnl": 1.2, "is_shadow": False},
                "ETH/USDT": {"pnl": -0.5, "is_shadow": True},
            },
            balance=100.0,
            available_balance=80.0,
            _start_ts=100.0,
            is_paused=False,
            halt_system_active=False,
            integrity_lock_active=False,
            circuit_breaker_active=False,
            ml_healthy=True,
            log=MagicMock(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with (
                    patch("core.metrics_export.time.time", return_value=160.0),
                    patch(
                        "core.metrics_export.collect_telemetry", return_value={"ts": "T", "db": 1}
                    ),
                ):
                    summary = export_metrics_summary(bot)
                written = json.loads(Path("logs/metrics_summary.json").read_text(encoding="utf-8"))
            finally:
                os.chdir(cwd)

        self.assertEqual(summary["real_open_trades"], 1)
        self.assertEqual(summary["shadow_open_trades"], 1)
        self.assertEqual(summary["uptime_s"], 60.0)
        self.assertEqual(written["telemetry"], {"db": 1})


if __name__ == "__main__":
    unittest.main()

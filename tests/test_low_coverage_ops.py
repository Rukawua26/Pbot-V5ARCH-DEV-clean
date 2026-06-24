import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
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

        bot = SimpleNamespace(is_running=True, api_status="", log=MagicMock())

        def fetch_status():
            bot.is_running = False
            return {"status": "ok"}

        bot.execution = SimpleNamespace(exchange=SimpleNamespace(fetch_status=fetch_status))
        with patch("core.bot_runtime_ops.time.sleep"):
            heartbeat_loop(bot)

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

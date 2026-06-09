import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("SNIPER_API_KEY", "test-key-for-tests")

import tools.dashboard as dashboard
from core import cmd_consumer, state_snapshot
from tools.dashboard import api_server


class _DummyBot:
    def __init__(self):
        self.balance_lock = threading.Lock()
        self.lock = threading.Lock()
        self.scanner_lock = threading.Lock()
        self.balance = 110.0
        self.available_balance = 95.0
        self.daily_initial_balance = 100.0
        self.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "entry": 100.123456789,
                "size_usd": 25.0,
                "pnl": 1.234,
                "entry_confidence": 72.5,
            },
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "SELL",
                "entry_price": 2000.0,
                "size": 15.0,
                "pnl_pct": -2.345,
                "confidence": 61.0,
                "is_shadow": True,
            },
        }
        self.scanner_history = [
            {
                "symbol": "BTC/USDT",
                "signal": "BUY",
                "ml_score": 72.5,
                "rsi_val": 55,
                "trend_val": "UP",
                "result": "PASS",
                "tier": "GOLD",
            }
        ]
        self.pairs_to_scan = ["BTC/USDT", "ETH/USDT"]
        self.is_running = False
        self.handled = []
        self.logs = []

    def handle_command(self, action):
        self.handled.append(action)

    def log(self, message):
        self.logs.append(message)


class DashboardIpcTest(unittest.TestCase):
    def test_state_snapshot_writes_atomic_dashboard_state(self):
        bot = _DummyBot()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            with patch.object(state_snapshot, "STATE_FILE", path):
                state_snapshot._write_state_snapshot(bot)

            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)

        self.assertEqual(data["balance"], 110.0)
        self.assertEqual(data["daily_pnl_pct"], 10.0)
        self.assertEqual(data["active_trades_count"], 1)
        self.assertEqual(data["shadow_trades_count"], 1)
        self.assertEqual(data["active_trades"][0]["entry_price"], 100.12345679)
        self.assertEqual(data["active_trades"][0]["size"], 25.0)
        self.assertEqual(data["active_trades"][0]["pnl_pct"], 1.23)
        self.assertEqual(data["active_trades"][0]["confidence"], 72.5)
        self.assertEqual(data["shadow_trades"][0]["entry_price"], 2000.0)
        self.assertEqual(data["radar"][0]["symbol"], "BTC/USDT")
        self.assertIn("ETH/USDT", [item["symbol"] for item in data["radar"]])
        pending = [item for item in data["radar"] if item["symbol"] == "ETH/USDT"][0]
        self.assertEqual(pending["result"], "PENDIENTE")

    def test_command_consumer_accepts_only_dashboard_commands(self):
        bot = _DummyBot()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "command.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"commands": [{"action": "/pause"}, {"action": "/dump_db"}]},
                    handle,
                )

            with patch.object(cmd_consumer, "CMD_FILE", path):
                cmd_consumer.consume_command_file(bot)

            self.assertFalse(os.path.exists(path))

        self.assertEqual(bot.handled, ["/pause"])
        self.assertTrue(any("rejected" in message for message in bot.logs))

    def test_api_command_writer_rejects_non_whitelisted_command(self):
        with self.assertRaises(HTTPException) as raised:
            api_server.send_command(api_server.Command(action="/dump_db"), _=None)

        self.assertEqual(raised.exception.status_code, 400)

    def test_api_command_writer_persists_allowed_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(api_server, "CMD_DIR", tmpdir):
                result = api_server.send_command(
                    api_server.Command(action=" /recover_halt "), _=None
                )
                path = os.path.join(tmpdir, "command.json")
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)

        self.assertEqual(result, {"ok": True, "action": "/recover_halt"})
        self.assertEqual(data["commands"][0]["action"], "/recover_halt")

    def test_dashboard_startup_reuses_existing_localhost_server(self):
        bot = _DummyBot()

        with (
            patch.object(dashboard, "_dashboard_thread", None),
            patch.object(dashboard, "_is_port_open", return_value=True),
        ):
            handle = dashboard.start_dashboard(bot)

        self.assertTrue(handle.already_running)
        self.assertEqual(handle.host, "127.0.0.1")
        self.assertEqual(handle.port, 8000)
        self.assertTrue(any("localhost ya disponible" in message for message in bot.logs))

    def test_dashboard_startup_can_be_disabled_by_env(self):
        bot = _DummyBot()

        with (
            patch.dict(os.environ, {"SNIPER_DASHBOARD_AUTOSTART": "false"}),
            patch.object(dashboard, "_dashboard_thread", None),
        ):
            handle = dashboard.start_dashboard(bot)

        self.assertFalse(handle.enabled)
        self.assertTrue(any("deshabilitado" in message for message in bot.logs))


if __name__ == "__main__":
    unittest.main()

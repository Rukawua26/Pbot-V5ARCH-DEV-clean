import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from core.watchdog import resolve_watchdog_heartbeat_path, write_watchdog_heartbeat


class WatchdogHeartbeatTest(unittest.TestCase):
    def test_resolve_heartbeat_path_uses_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "heartbeat.json")
            resolved = resolve_watchdog_heartbeat_path(hb)
            self.assertEqual(resolved, hb)

    def test_resolve_heartbeat_path_falls_back_when_dir_missing(self):
        hb = os.path.join("/path/not/exists", "heartbeat.json")
        resolved = resolve_watchdog_heartbeat_path(hb)

        self.assertEqual(resolved, "/tmp/sniper_ai_heartbeat.json")

    def test_writes_heartbeat_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "heartbeat.json")
            bot = SimpleNamespace(_watchdog_last_write_mono=0.0)

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=0.0)

            self.assertTrue(os.path.exists(hb))
            with open(hb, encoding="utf-8") as handle:
                payload = json.loads(handle.read())
            self.assertIn("ts", payload)
            self.assertIn("ts_iso", payload)
            self.assertIn("pid", payload)

    def test_respects_min_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "heartbeat.json")
            bot = SimpleNamespace(_watchdog_last_write_mono=0.0)

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=9999.0)
            with open(hb, encoding="utf-8") as handle:
                first = json.loads(handle.read())["ts"]

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=9999.0)
            with open(hb, encoding="utf-8") as handle:
                second = json.loads(handle.read())["ts"]

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

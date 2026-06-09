import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from core.watchdog import write_watchdog_heartbeat


class TemporalInvarianceTest(unittest.TestCase):
    def test_watchdog_min_interval_uses_monotonic_under_wall_clock_jump(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat_path = os.path.join(tmp, "heartbeat.json")
            bot = SimpleNamespace(_watchdog_last_write_mono=0.0)

            with (
                patch("core.watchdog.monotonic_now", return_value=100.0),
                patch("core.watchdog.utc_now") as mock_utc,
            ):
                mock_utc.return_value = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
                write_watchdog_heartbeat(bot, path=heartbeat_path, min_interval_s=15.0)

            first_mtime = os.path.getmtime(heartbeat_path)

            with (
                patch("core.watchdog.monotonic_now", return_value=105.0),
                patch("core.watchdog.utc_now") as mock_utc,
            ):
                mock_utc.return_value = datetime.fromisoformat("2036-01-01T00:00:00+00:00")
                write_watchdog_heartbeat(bot, path=heartbeat_path, min_interval_s=15.0)

            second_mtime = os.path.getmtime(heartbeat_path)
            self.assertEqual(first_mtime, second_mtime)


if __name__ == "__main__":
    unittest.main()

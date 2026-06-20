import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class BotRuntimeMonitorTest(unittest.TestCase):
    def test_rotate_jsonl_rotates_existing_files(self):
        from core.bot_runtime_monitor import _rotate_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.jsonl"
            path.write_text("x" * 10, encoding="utf-8")
            (Path(tmp) / "runtime.jsonl.1").write_text("old", encoding="utf-8")

            _rotate_jsonl(str(path), max_bytes=1, backups=2)

            self.assertFalse(path.exists())
            self.assertTrue((Path(tmp) / "runtime.jsonl.1").exists())
            self.assertTrue((Path(tmp) / "runtime.jsonl.2").exists())

    def test_append_runtime_metric_writes_jsonl(self):
        from core.bot_runtime_monitor import append_runtime_metric

        bot = SimpleNamespace(log=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                append_runtime_metric(bot, {"event": "test"})
                rows = Path("logs/runtime_metrics.jsonl").read_text(encoding="utf-8").splitlines()
            finally:
                os.chdir(cwd)

        self.assertEqual(rows, ['{"event": "test"}'])
        bot.log.assert_not_called()

    def test_get_rss_mb_logs_and_returns_zero_on_read_error(self):
        from core.bot_runtime_monitor import get_rss_mb

        bot = SimpleNamespace(log=MagicMock())

        with patch("builtins.open", side_effect=OSError("no proc")):
            self.assertEqual(get_rss_mb(bot), 0.0)
        bot.log.assert_called_once()


if __name__ == "__main__":
    unittest.main()

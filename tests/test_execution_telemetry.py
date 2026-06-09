import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.execution_telemetry import append_execution_event


class ExecutionTelemetryTests(unittest.TestCase):
    def test_skips_file_telemetry_under_unittest_by_default(self):
        bot = SimpleNamespace(log=MagicMock())
        with tempfile.TemporaryDirectory():
            with patch("core.execution_telemetry.os.makedirs"):
                with patch.dict(os.environ, {}, clear=False):
                    with patch("core.execution_telemetry.open") as mocked_open:
                        with patch(
                            "core.execution_telemetry._should_skip_file_telemetry",
                            return_value=True,
                        ):
                            append_execution_event(bot, "EVENT", {"k": "v"})
        mocked_open.assert_not_called()

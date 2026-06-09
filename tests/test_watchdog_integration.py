#!/usr/bin/env python3
"""Integration tests for watchdog system (heartbeat write + external supervisor)."""

import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.watchdog import (
    DEFAULT_WATCHDOG_HEARTBEAT_PATH,
    FALLBACK_WATCHDOG_HEARTBEAT_PATH,
    resolve_watchdog_heartbeat_path,
    write_watchdog_heartbeat,
)


class TestWatchdogHeartbeatPathResolution(unittest.TestCase):
    """Tests for heartbeat path resolution logic."""

    def test_default_path(self):
        path = resolve_watchdog_heartbeat_path()
        self.assertEqual(path, DEFAULT_WATCHDOG_HEARTBEAT_PATH)

    def test_env_override(self):
        with patch.dict(os.environ, {"WATCHDOG_HEARTBEAT_PATH": "/tmp/custom_heartbeat.json"}):
            path = resolve_watchdog_heartbeat_path()
            self.assertEqual(path, "/tmp/custom_heartbeat.json")

    def test_explicit_path(self):
        path = resolve_watchdog_heartbeat_path("/var/run/heartbeat.json")
        self.assertEqual(path, "/var/run/heartbeat.json")

    def test_fallback_when_dir_missing(self):
        nonexistent_dir = "/nonexistent_dir_12345/heartbeat.json"
        path = resolve_watchdog_heartbeat_path(nonexistent_dir)
        self.assertEqual(path, FALLBACK_WATCHDOG_HEARTBEAT_PATH)


class TestWatchdogHeartbeatWrite(unittest.TestCase):
    """Tests for heartbeat write logic."""

    def setUp(self):
        self.tmp_path = "/tmp/test_sniper_heartbeat.json"
        self.bot = MagicMock()
        self.bot._watchdog_last_write_by_path = {}

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)
        fallback = "/tmp/sniper_ai_heartbeat.json"
        if os.path.exists(fallback):
            os.remove(fallback)

    def test_write_creates_valid_json(self):
        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=0)
        self.assertTrue(os.path.exists(self.tmp_path))
        with open(self.tmp_path, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["status"], "alive")
        self.assertIn("ts", payload)
        self.assertIn("pid", payload)

    def test_write_respects_min_interval(self):
        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=60)
        first_ts = self.bot._watchdog_last_write_by_path.get(self.tmp_path)

        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=60)
        second_ts = self.bot._watchdog_last_write_by_path.get(self.tmp_path)

        self.assertEqual(first_ts, second_ts)

    def test_write_updates_after_interval(self):
        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=0)
        first_ts = self.bot._watchdog_last_write_by_path.get(self.tmp_path)

        time.sleep(0.05)
        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=0.01)
        second_ts = self.bot._watchdog_last_write_by_path.get(self.tmp_path)

        self.assertNotEqual(first_ts, second_ts)

    def test_write_uses_atomic_replace(self):
        tmp_file = os.path.join(os.path.dirname(self.tmp_path), ".sniper_ai_heartbeat.tmp")
        write_watchdog_heartbeat(self.bot, path=self.tmp_path, min_interval_s=0)
        self.assertFalse(os.path.exists(tmp_file))
        self.assertTrue(os.path.exists(self.tmp_path))


class TestWatchdogSupervisor(unittest.TestCase):
    """Tests for external watchdog supervisor logic."""

    def test_read_missing_heartbeat_returns_zero(self):
        from tools.watchdog_supervisor import read_heartbeat_ts

        ts = read_heartbeat_ts(Path("/tmp/nonexistent_heartbeat_12345.json"))
        self.assertEqual(ts, 0.0)

    def test_read_valid_heartbeat(self):
        from tools.watchdog_supervisor import read_heartbeat_ts

        test_path = Path("/tmp/test_supervisor_heartbeat.json")
        payload = {"ts": time.time(), "status": "alive"}
        with open(test_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        ts = read_heartbeat_ts(test_path)
        self.assertGreater(ts, 0.0)

        os.remove(test_path)

    def test_stale_heartbeat_detection(self):
        from tools.watchdog_supervisor import read_heartbeat_ts

        test_path = Path("/tmp/test_stale_heartbeat.json")
        old_ts = time.time() - 100
        payload = {"ts": old_ts, "status": "alive"}
        with open(test_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        ts = read_heartbeat_ts(test_path)
        now = time.time()
        self.assertTrue((now - ts) > 45)

        os.remove(test_path)

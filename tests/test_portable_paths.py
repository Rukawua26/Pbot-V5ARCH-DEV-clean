import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import portable_paths


class PortablePathsTest(unittest.TestCase):
    def test_dev_base_defaults_to_project_root(self):
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.dict(os.environ, {}, clear=False),
        ):
            base = portable_paths.portable_base_dir()

        self.assertEqual(base, portable_paths.project_root())

    def test_windows_frozen_uses_appdata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "win32"),
                patch.dict(os.environ, {"APPDATA": tmpdir}, clear=False),
            ):
                base = portable_paths.portable_base_dir()

        self.assertEqual(base, Path(tmpdir) / "SniperBot")

    def test_linux_frozen_uses_config_dir(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "platform", "linux"):
            base = portable_paths.portable_base_dir()

        self.assertEqual(base, Path.home() / ".config" / "SniperBot")

    def test_explicit_portable_base_sets_runtime_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"SNIPER_PORTABLE_BASE_DIR": tmpdir}
            with patch.dict(os.environ, env, clear=False):
                base = portable_paths.configure_runtime_environment()
                db_path = os.environ.get("SNIPER_DB_PATH")

        self.assertEqual(base, Path(tmpdir).resolve())
        self.assertTrue(str(db_path).endswith("data/sniper_brain.db"))


if __name__ == "__main__":
    unittest.main()

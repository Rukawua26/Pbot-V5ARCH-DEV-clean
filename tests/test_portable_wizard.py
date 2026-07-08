import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config.wizard import ejecutar_multi_wizard, obtener_ip_publica


class PortableWizardTest(unittest.TestCase):
    def test_obtener_ip_publica_handles_network_errors(self):
        with patch("core.config.wizard.requests.get", side_effect=RuntimeError("offline")):
            self.assertIn("No detectada", obtener_ip_publica())

    def test_wizard_generates_paper_only_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            env_path = base / ".env"
            with (
                patch("core.config.wizard._clear_screen"),
                patch("core.config.wizard.obtener_ip_publica", return_value="203.0.113.10"),
                patch("builtins.input", return_value="api-key"),
                patch("getpass.getpass", side_effect=["api-secret", "dashboard-key-123456"]),
            ):
                ejecutar_multi_wizard(base, env_path)

            content = env_path.read_text(encoding="utf-8")

        self.assertIn("PAPER_MODE=true", content)
        self.assertIn("ALLOW_REAL_TRADING=false", content)
        self.assertIn("BINANCE_API_KEY=api-key", content)
        self.assertIn("BINANCE_API_SECRET=api-secret", content)
        self.assertNotIn("ALLOW_REAL_TRADING=true", content)


if __name__ == "__main__":
    unittest.main()

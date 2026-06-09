import json
import tempfile
import unittest
from pathlib import Path

from core.config.hyperopt_loader import HyperoptConfigLoader


class HyperoptConfigLoaderTest(unittest.TestCase):
    def setUp(self):
        self.original_path = HyperoptConfigLoader._path
        HyperoptConfigLoader._cache = None

    def tearDown(self):
        HyperoptConfigLoader._path = self.original_path
        HyperoptConfigLoader._cache = None

    def _write_config(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
        with tmp:
            json.dump(payload, tmp)
        HyperoptConfigLoader._path = Path(tmp.name)
        HyperoptConfigLoader._cache = None

    def test_legacy_params_remain_supported(self):
        self._write_config({"enabled": True, "params": {"stop_loss_pct": 1.2}})

        self.assertTrue(HyperoptConfigLoader.is_enabled())
        self.assertEqual(HyperoptConfigLoader.get_param("stop_loss_pct"), 1.2)

    def test_symbol_params_override_global_params(self):
        self._write_config(
            {
                "enabled": True,
                "params": {"stop_loss_pct": 1.2, "take_profit_pct": 2.0},
                "symbols": {"BTC/USDT": {"stop_loss_pct": 0.8}},
            }
        )

        params = HyperoptConfigLoader.get_params_for_symbol("BTC/USDT:USDT")

        self.assertEqual(params["stop_loss_pct"], 0.8)
        self.assertEqual(params["take_profit_pct"], 2.0)

    def test_unknown_symbol_uses_global_params(self):
        self._write_config(
            {
                "enabled": True,
                "params": {"stop_loss_pct": 1.2},
                "symbols": {"BTC/USDT": {"stop_loss_pct": 0.8}},
            }
        )

        params = HyperoptConfigLoader.get_params_for_symbol("ETH/USDT")

        self.assertEqual(params["stop_loss_pct"], 1.2)

    def test_malformed_config_falls_back_to_defaults(self):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
        with tmp:
            tmp.write("not json")
        HyperoptConfigLoader._path = Path(tmp.name)
        HyperoptConfigLoader._cache = None

        self.assertFalse(HyperoptConfigLoader.is_enabled())
        self.assertEqual(HyperoptConfigLoader.get_param("alma_offset"), 0.85)


if __name__ == "__main__":
    unittest.main()

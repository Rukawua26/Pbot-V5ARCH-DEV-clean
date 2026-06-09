import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config import Config
from core.regime_tuning import (
    _STATS_PATH,
    get_sl_multiplier,
    get_stats_summary,
    get_tp_multiplier,
    record_trade,
)


class RegimeTuningTests(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()
        # Use a temp path for stats to avoid polluting real data
        self._orig_path = _STATS_PATH
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _set_stats_path(self, path: Path):
        import core.regime_tuning as rt

        rt._STATS_PATH = path
        # Reset module-level cache if any

    def test_record_trade_creates_stats_file(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        with patch.object(Config, "REGIME_TUNING_ENABLED", True):
            record_trade("BULL_TREND", 2.5)
        self.assertTrue(tmp.exists())
        data = json.loads(tmp.read_text())
        self.assertIn("BULL_TREND", data)
        self.assertEqual(data["BULL_TREND"]["trades"], 1)
        self.assertEqual(data["BULL_TREND"]["wins"], 1)

    def test_record_trade_loss(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        record_trade("BEAR_TREND", -1.5)
        data = json.loads(tmp.read_text())
        self.assertEqual(data["BEAR_TREND"]["trades"], 1)
        self.assertEqual(data["BEAR_TREND"]["wins"], 0)

    def test_record_trade_tracks_multiple(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        record_trade("RANGE", 1.0)
        record_trade("RANGE", -0.5)
        record_trade("RANGE", 2.0)
        data = json.loads(tmp.read_text())
        self.assertEqual(data["RANGE"]["trades"], 3)
        self.assertEqual(data["RANGE"]["wins"], 2)
        self.assertAlmostEqual(data["RANGE"]["sum_pnl"], 2.5)

    def test_disabled_returns_one(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        with patch.object(Config, "REGIME_TUNING_ENABLED", False):
            mult = get_sl_multiplier(self.bot, "BULL_TREND")
        self.assertEqual(mult, 1.0)

    def test_insufficient_trades_returns_one(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        with (
            patch.object(Config, "REGIME_TUNING_ENABLED", True),
            patch.object(Config, "REGIME_TUNING_MIN_TRADES", 5),
        ):
            mult = get_sl_multiplier(self.bot, "BULL_TREND")
        self.assertEqual(mult, 1.0)

    def test_low_winrate_tightens_sl(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        # 10 trades, 2 wins = 20% WR (< 35%)
        for _ in range(8):
            record_trade("RANGE", -1.0)
        for _ in range(2):
            record_trade("RANGE", 1.0)
        with (
            patch.object(Config, "REGIME_TUNING_ENABLED", True),
            patch.object(Config, "REGIME_TUNING_MIN_TRADES", 5),
            patch.object(Config, "REGIME_TUNING_SL_RANGE_MIN", 0.60),
        ):
            mult = get_sl_multiplier(self.bot, "RANGE")
        self.assertLess(mult, 0.7)

    def test_high_winrate_loosens_sl(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        # 10 trades, 7 wins = 70% WR (> 65%)
        for _ in range(3):
            record_trade("BULL_TREND", -1.0)
        for _ in range(7):
            record_trade("BULL_TREND", 1.0)
        with (
            patch.object(Config, "REGIME_TUNING_ENABLED", True),
            patch.object(Config, "REGIME_TUNING_MIN_TRADES", 5),
            patch.object(Config, "REGIME_TUNING_SL_RANGE_MAX", 1.20),
        ):
            mult = get_sl_multiplier(self.bot, "BULL_TREND")
        self.assertGreater(mult, 1.15)

    def test_tp_multiplier_follows_winrate(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        for _ in range(6):
            record_trade("BULL_TREND", 1.0)
        with (
            patch.object(Config, "REGIME_TUNING_ENABLED", True),
            patch.object(Config, "REGIME_TUNING_MIN_TRADES", 5),
        ):
            mult = get_tp_multiplier(self.bot, "BULL_TREND")
        self.assertGreater(mult, 1.1)

    def test_unrecognized_regime_returns_one(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        with patch.object(Config, "REGIME_TUNING_ENABLED", True):
            mult = get_sl_multiplier(self.bot, "UNKNOWN")
        self.assertEqual(mult, 1.0)

    def test_get_stats_summary_empty(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        summary = get_stats_summary()
        self.assertIn("sin datos", summary)

    def test_get_stats_summary_with_data(self):
        tmp = Path(self.tmp_dir) / "regime_tuning_stats.json"
        self._set_stats_path(tmp)
        record_trade("BULL_TREND", 1.0)
        record_trade("BULL_TREND", 1.0)
        record_trade("RANGE", -1.0)
        summary = get_stats_summary()
        self.assertIn("BULL_TREND", summary)
        self.assertIn("RANGE", summary)


class RegimeTuningRiskEngineIntegrationTests(unittest.TestCase):
    def test_risk_engine_accepts_regime_mult_params(self):
        from core.risk_engine import RiskEngine

        brain = MagicMock()
        engine = RiskEngine(brain)
        engine.hyperopt_enabled = True
        engine.stop_loss_pct = 2.0
        engine.take_profit_pct = 5.0

        sl, tp, mode = engine.get_exit_levels(
            entry_price=100.0,
            side="BUY",
            atr=2.0,
            trend="RANGO",
            regime_sl_mult=0.75,
            regime_tp_mult=1.20,
        )
        self.assertEqual(mode, "HYPEROPT_FIXED")
        # SL = 100 * (1 - 2.0/100 * 0.75) = 100 * (1 - 0.015) = 98.5
        self.assertAlmostEqual(sl, 98.5, places=4)
        # TP = 100 * (1 + 5.0/100 * 1.20) = 100 * (1 + 0.06) = 106.0
        self.assertAlmostEqual(tp, 106.0, places=4)

    def test_risk_engine_dynamic_atr_path_applies_mult(self):
        from core.risk_engine import RiskEngine
        from tools.strategy import Strategy

        brain = MagicMock()
        engine = RiskEngine(brain)
        engine.hyperopt_enabled = False

        with (
            patch.object(Strategy, "get_stop_loss", return_value=95.0),
            patch.object(Strategy, "get_take_profit", return_value=110.0),
        ):
            sl, tp, mode = engine.get_exit_levels(
                entry_price=100.0,
                side="BUY",
                atr=2.0,
                trend="RANGO",
                modifier=1.0,
                regime_sl_mult=0.80,
                regime_tp_mult=1.10,
            )
        self.assertEqual(mode, "DYNAMIC_ATR")
        # modifier * regime_sl_mult = 1.0 * 0.80 = 0.80 passed to get_stop_loss
        # TP = 100 + (110 - 100) * 1.10 = 111.0
        self.assertAlmostEqual(tp, 111.0, places=4)


if __name__ == "__main__":
    unittest.main()

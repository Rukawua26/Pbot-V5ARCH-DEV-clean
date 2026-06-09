import os
import sqlite3

# We need to mock Config before importing risk_engine
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestGetDailyPnlPct(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_trades_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                pnl REAL,
                timestamp TEXT,
                is_shadow INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def test_returns_zero_when_balance_zero(self):
        from core.risk_engine import get_daily_pnl_pct

        pct, usd = get_daily_pnl_pct(self.db_path, 0.0)
        self.assertEqual(pct, 0.0)
        self.assertEqual(usd, 0.0)

    def test_returns_zero_when_balance_negative(self):
        from core.risk_engine import get_daily_pnl_pct

        pct, usd = get_daily_pnl_pct(self.db_path, -100.0)
        self.assertEqual(pct, 0.0)

    def test_calculates_pnl_correctly(self):
        from core.risk_engine import get_daily_pnl_pct

        conn = self._create_trades_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        conn.executemany(
            "INSERT INTO trades (symbol, pnl, timestamp, is_shadow) VALUES (?, ?, ?, ?)",
            [
                ("BTC/USDT", 10.0, f"{today} 10:00:00", 0),
                ("ETH/USDT", 20.0, f"{today} 11:00:00", 0),
                ("BTC/USDT", -5.0, f"{today} 12:00:00", 0),
            ],
        )
        conn.commit()
        conn.close()

        pct, usd = get_daily_pnl_pct(self.db_path, 1000.0)
        self.assertEqual(usd, 25.0)
        self.assertEqual(pct, 0.025)

    def test_ignores_shadow_trades(self):
        from core.risk_engine import get_daily_pnl_pct

        conn = self._create_trades_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        conn.executemany(
            "INSERT INTO trades (symbol, pnl, timestamp, is_shadow) VALUES (?, ?, ?, ?)",
            [
                ("BTC/USDT", 100.0, f"{today} 10:00:00", 0),
                ("BTC/USDT", 50.0, f"{today} 11:00:00", 1),
            ],
        )
        conn.commit()
        conn.close()

        pct, usd = get_daily_pnl_pct(self.db_path, 1000.0)
        self.assertEqual(usd, 100.0)

    def test_ignores_other_days_trades(self):
        from core.risk_engine import get_daily_pnl_pct

        conn = self._create_trades_table()
        conn.executemany(
            "INSERT INTO trades (symbol, pnl, timestamp, is_shadow) VALUES (?, ?, ?, ?)",
            [
                ("BTC/USDT", 100.0, "2025-01-01 10:00:00", 0),
            ],
        )
        conn.commit()
        conn.close()

        pct, usd = get_daily_pnl_pct(self.db_path, 1000.0)
        self.assertEqual(usd, 0.0)

    @patch("core.risk_engine.logging")
    def test_handles_db_error_gracefully(self, mock_logging):
        from core.risk_engine import get_daily_pnl_pct

        pct, usd = get_daily_pnl_pct("/nonexistent/path.db", 1000.0)
        self.assertIsNone(pct)
        self.assertIsNone(usd)


class TestRiskEngineDailyDrawdown(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())

    def test_check_daily_drawdown_fails_closed_when_unverified(self):
        self.engine.brain.get_daily_real_pnl = MagicMock(side_effect=RuntimeError("db down"))

        allowed, reason = self.engine.check_daily_drawdown(1000.0)

        self.assertFalse(allowed)
        self.assertEqual(reason, "DAILY_DRAWDOWN_UNVERIFIED")

    def test_check_daily_drawdown_blocks_when_pnl_is_none(self):
        self.engine.brain.get_daily_real_pnl = MagicMock(return_value=(None, None))

        allowed, reason = self.engine.check_daily_drawdown(1000.0)

        self.assertFalse(allowed)
        self.assertEqual(reason, "DAILY_DRAWDOWN_UNVERIFIED")


class TestRiskEngineGetExitLevels(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                from core.risk_engine import RiskEngine

                self.engine = RiskEngine(brain=MagicMock())

    def test_returns_invalid_entry_when_price_zero(self):
        sl, tp, label = self.engine.get_exit_levels(0.0, "BUY", 100.0, "UP")
        self.assertEqual(sl, 0.0)
        self.assertEqual(label, "INVALID_ENTRY")

    def test_returns_invalid_entry_when_price_negative(self):
        sl, tp, label = self.engine.get_exit_levels(-100.0, "BUY", 100.0, "UP")
        self.assertEqual(label, "INVALID_ENTRY")

    @patch("core.risk_engine.HyperoptConfigLoader")
    def test_uses_hyperopt_when_enabled(self, mock_hyperopt):
        mock_hyperopt.is_enabled.return_value = True
        mock_hyperopt.get_param.side_effect = lambda name, default: {
            "stop_loss_pct": 2.0,
            "take_profit_pct": 5.0,
        }.get(name, default)
        mock_hyperopt.get_params_for_symbol.return_value = {
            "stop_loss_pct": 2.0,
            "take_profit_pct": 5.0,
        }

        with patch("core.risk_engine.CrashPredictor"):
            from core.risk_engine import RiskEngine

            engine = RiskEngine(brain=MagicMock())

        sl, tp, label = engine.get_exit_levels(100.0, "BUY", 1.0, "UP")
        self.assertEqual(label, "HYPEROPT_FIXED")
        self.assertEqual(sl, 98.0)
        self.assertEqual(tp, 105.0)

    @patch("core.risk_engine.HyperoptConfigLoader")
    def test_uses_hyperopt_for_sell_side(self, mock_hyperopt):
        mock_hyperopt.is_enabled.return_value = True
        mock_hyperopt.get_param.side_effect = lambda name, default: {
            "stop_loss_pct": 2.0,
            "take_profit_pct": 5.0,
        }.get(name, default)
        mock_hyperopt.get_params_for_symbol.return_value = {
            "stop_loss_pct": 2.0,
            "take_profit_pct": 5.0,
        }

        with patch("core.risk_engine.CrashPredictor"):
            from core.risk_engine import RiskEngine

            engine = RiskEngine(brain=MagicMock())

        sl, tp, label = engine.get_exit_levels(100.0, "SELL", 1.0, "DOWN")
        self.assertEqual(label, "HYPEROPT_FIXED")
        self.assertEqual(sl, 102.0)
        self.assertEqual(tp, 95.0)

    @patch("core.risk_engine.HyperoptConfigLoader")
    def test_uses_symbol_hyperopt_params_when_symbol_is_provided(self, mock_hyperopt):
        mock_hyperopt.is_enabled.return_value = True
        mock_hyperopt.get_param.side_effect = lambda name, default: {
            "stop_loss_pct": 2.0,
            "take_profit_pct": 5.0,
        }.get(name, default)
        mock_hyperopt.get_params_for_symbol.return_value = {
            "stop_loss_pct": 1.0,
            "take_profit_pct": 3.0,
        }

        with patch("core.risk_engine.CrashPredictor"):
            from core.risk_engine import RiskEngine

            engine = RiskEngine(brain=MagicMock())

        sl, tp, label = engine.get_exit_levels(100.0, "BUY", 1.0, "UP", symbol="BTC/USDT")
        self.assertEqual(label, "HYPEROPT_SYMBOL")
        self.assertEqual(sl, 99.0)
        self.assertEqual(tp, 103.0)


class TestCalculatePositionSize(unittest.TestCase):
    def setUp(self):
        with patch("core.risk_engine.CrashPredictor"):
            with patch("core.risk_engine.HyperoptConfigLoader") as mock_hyperopt:
                mock_hyperopt.is_enabled.return_value = False
                with patch("core.risk_engine.Config") as mock_config:
                    mock_config.MIN_NOTIONAL_VALUE = 12.0
                    mock_config.MAX_MARGIN_PERCENT = 5.0
                    mock_config.MAX_RISK_USD = 1.0
                    mock_config.STOP_LOSS_ATR_MODIFIER = 1.0
                    from core.risk_engine import RiskEngine

                    self.engine = RiskEngine(brain=MagicMock())

    def test_returns_zero_when_capital_insufficient(self):
        with patch.object(self.engine, "hyperopt_enabled", False):
            with patch("core.risk_engine.Config") as mock_config:
                mock_config.MIN_NOTIONAL_VALUE = 100.0
                mock_config.MAX_MARGIN_PERCENT = 1.0
                mock_config.MAX_RISK_USD = 100.0
                mock_config.STOP_LOSS_ATR_MODIFIER = 1.0

                context = {"prob_final": 0.5, "atr_pct": 0.02}
                amount, notional = self.engine.calculate_position_size(
                    10.0, "BTC/USDT", 100.0, 1, context
                )
                self.assertEqual(amount, 0)
                self.assertEqual(notional, -1)

    def test_uses_base_notional_when_confidence_low(self):
        context = {"prob_final": 0.50, "atr_pct": 0.02}
        amount, notional = self.engine.calculate_position_size(
            1000.0, "BTC/USDT", 100.0, 10, context, is_shadow=True
        )
        self.assertGreater(amount, 0)
        self.assertAlmostEqual(notional, 5.0, delta=0.01)

    def test_scales_with_confidence_kelly(self):
        context = {"prob_final": 0.80, "atr_pct": 0.02}
        amount, notional = self.engine.calculate_position_size(
            1000.0, "BTC/USDT", 100.0, 10, context, is_shadow=True
        )
        self.assertGreater(notional, 5.0)

    def test_returns_error_code_for_excessive_risk(self):
        with patch("core.risk_engine.Config") as mock_config:
            mock_config.MIN_NOTIONAL_VALUE = 12.0
            mock_config.MAX_MARGIN_PERCENT = 50.0
            mock_config.MAX_RISK_USD = 0.5
            mock_config.STOP_LOSS_ATR_MODIFIER = 1.0

            context = {"prob_final": 1.0, "atr_pct": 0.10}
            amount, notional = self.engine.calculate_position_size(
                1000.0, "BTC/USDT", 100.0, 10, context
            )
            self.assertEqual(amount, 0)
            self.assertEqual(notional, -4)

    def test_shadow_mode_returns_notional_directly(self):
        context = {"prob_final": 0.70, "atr_pct": 0.02}
        amount, notional = self.engine.calculate_position_size(
            1000.0, "BTC/USDT", 100.0, 10, context, is_shadow=True
        )
        self.assertGreater(amount, 0)
        self.assertAlmostEqual(amount * 100.0, notional, delta=0.01)


if __name__ == "__main__":
    unittest.main()

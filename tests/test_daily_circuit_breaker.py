import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_main_loop import check_daily_drawdown_breaker
from core.risk_engine import get_daily_pnl_pct


def _create_trade_db(rows):
    tmpdir = tempfile.TemporaryDirectory()
    db_path = Path(tmpdir.name) / "sniper_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE trades (
            timestamp TEXT,
            pnl REAL,
            is_shadow INTEGER DEFAULT 0
        )
        """
    )
    conn.executemany("INSERT INTO trades (timestamp, pnl, is_shadow) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return tmpdir, db_path


class DailyCircuitBreakerTest(unittest.TestCase):
    def test_daily_pnl_pct_uses_utc_real_trades_only(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        tmpdir, db_path = _create_trade_db(
            [
                (f"{today}T01:00:00+00:00", -20.0, 0),
                (f"{today}T02:00:00+00:00", -15.0, 0),
                (f"{today}T03:00:00+00:00", -99.0, 1),
                (f"{yesterday}T23:00:00+00:00", -500.0, 0),
            ]
        )
        try:
            pct, usd = get_daily_pnl_pct(db_path, 1000.0)
            self.assertEqual(usd, -35.0)
            self.assertAlmostEqual(pct, -0.035)
        finally:
            tmpdir.cleanup()

    @patch("core.bot_main_loop.Config.PAPER_MODE", True)
    @patch("core.risk_policy.send_telegram_msg")
    def test_breaker_does_not_apply_in_paper(self, mocked_send):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        tmpdir, db_path = _create_trade_db([(f"{today}T01:00:00+00:00", -100.0, 0)])
        try:
            bot = SimpleNamespace(
                brain=SimpleNamespace(db_name=str(db_path)),
                get_current_balance=MagicMock(return_value=1000.0),
                log=MagicMock(),
                circuit_breaker_active=False,
                is_paused=False,
                daily_drawdown_alert_sent=False,
            )
            self.assertFalse(check_daily_drawdown_breaker(bot))
            self.assertFalse(bot.circuit_breaker_active)
            self.assertFalse(bot.is_paused)
            mocked_send.assert_not_called()
        finally:
            tmpdir.cleanup()

    @patch("core.bot_main_loop.Config.PAPER_MODE", False)
    @patch("core.risk_policy.send_telegram_msg")
    @patch("core.bot_main_loop.get_daily_pnl_pct", return_value=(None, None))
    def test_breaker_fails_closed_when_drawdown_is_unverifiable(self, _mock_drawdown, mocked_send):
        bot = SimpleNamespace(
            brain=SimpleNamespace(db_name="missing.db"),
            get_current_balance=MagicMock(return_value=1000.0),
            log=MagicMock(),
            circuit_breaker_active=False,
            is_paused=False,
            daily_drawdown_alert_sent=False,
        )

        self.assertTrue(check_daily_drawdown_breaker(bot))
        self.assertTrue(bot.circuit_breaker_active)
        self.assertTrue(bot.is_paused)
        mocked_send.assert_called_once()

    @patch("core.bot_main_loop.Config.MAX_DAILY_DRAWDOWN_PCT", 0.03)
    @patch("core.bot_main_loop.Config.PAPER_MODE", False)
    @patch("core.risk_policy.send_telegram_msg")
    def test_breaker_activates_once_in_real_when_limit_is_breached(self, mocked_send):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        tmpdir, db_path = _create_trade_db([(f"{today}T01:00:00+00:00", -31.0, 0)])
        try:
            bot = SimpleNamespace(
                brain=SimpleNamespace(db_name=str(db_path)),
                get_current_balance=MagicMock(return_value=1000.0),
                log=MagicMock(),
                circuit_breaker_active=False,
                is_paused=False,
                daily_drawdown_alert_sent=False,
            )
            self.assertTrue(check_daily_drawdown_breaker(bot))
            self.assertTrue(bot.circuit_breaker_active)
            self.assertTrue(bot.is_paused)
            mocked_send.assert_called_once()

            self.assertTrue(check_daily_drawdown_breaker(bot))
            mocked_send.assert_called_once()
        finally:
            tmpdir.cleanup()

    @patch("core.bot_main_loop.Config.MAX_DAILY_DRAWDOWN_PCT", 0.03)
    @patch("core.bot_main_loop.Config.PAPER_MODE", False)
    @patch("core.risk_policy.send_telegram_msg")
    def test_breaker_stays_open_when_drawdown_is_above_limit(self, mocked_send):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        tmpdir, db_path = _create_trade_db([(f"{today}T01:00:00+00:00", -29.0, 0)])
        try:
            bot = SimpleNamespace(
                brain=SimpleNamespace(db_name=str(db_path)),
                get_current_balance=MagicMock(return_value=1000.0),
                log=MagicMock(),
                circuit_breaker_active=False,
                is_paused=False,
                daily_drawdown_alert_sent=False,
            )
            self.assertFalse(check_daily_drawdown_breaker(bot))
            self.assertFalse(bot.circuit_breaker_active)
            self.assertFalse(bot.is_paused)
            mocked_send.assert_not_called()
        finally:
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()

import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class DrawdownWarningTests(unittest.TestCase):
    def _bot(self):
        return SimpleNamespace(
            lock=RLock(),
            log=MagicMock(),
            daily_initial_balance=100.0,
            balance=100.0,
            peak_pnl=0.0,
            current_target=5.0,
            circuit_breaker_active=False,
            is_paused=False,
            halt_system_active=False,
            integrity_lock_active=False,
            mandatory_train_pending=False,
            daily_drawdown_alert_sent=False,
            _drawdown_warning_sent=False,
            _circuit_breaker_alert_sent=True,
        )

    @patch("core.bot_runtime_safety.send_telegram_msg")
    @patch("core.bot_runtime_safety.append_execution_event")
    @patch("core.bot_runtime_safety.Config.DAILY_LOSS_LIMIT", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_TRAILING_STOP", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_GOALS", [5.0, 10.0, 15.0])
    def test_drawdown_warning_fires_at_80_percent(self, mock_evt, mock_tg):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()
        pnl_at_80pct = -2.5

        result = check_safety_and_goals(bot, current_pnl=pnl_at_80pct)

        self.assertTrue(result)
        self.assertTrue(bot._drawdown_warning_sent)
        events = [call.args[1] for call in mock_evt.call_args_list]
        self.assertIn("DAILY_DRAWDOWN_WARNING", events)
        mock_tg.assert_called_once()

    @patch("core.bot_runtime_safety.send_telegram_msg")
    @patch("core.bot_runtime_safety.append_execution_event")
    @patch("core.bot_runtime_safety.Config.DAILY_LOSS_LIMIT", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_TRAILING_STOP", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_GOALS", [5.0, 10.0, 15.0])
    def test_drawdown_warning_does_not_fire_below_80_percent(self, mock_evt, mock_tg):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()
        pnl_below_80pct = -1.5

        result = check_safety_and_goals(bot, current_pnl=pnl_below_80pct)

        self.assertTrue(result)
        self.assertFalse(bot._drawdown_warning_sent)
        warning_events = [
            call for call in mock_evt.call_args_list if call.args[1] == "DAILY_DRAWDOWN_WARNING"
        ]
        self.assertEqual(len(warning_events), 0)

    @patch("core.bot_runtime_safety.send_telegram_msg")
    @patch("core.bot_runtime_safety.append_execution_event")
    @patch("core.bot_runtime_safety.Config.DAILY_LOSS_LIMIT", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_TRAILING_STOP", 3.0)
    @patch("core.bot_runtime_safety.Config.DAILY_GOALS", [5.0, 10.0, 15.0])
    def test_drawdown_warning_fires_only_once(self, mock_evt, _mock_tg):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()

        check_safety_and_goals(bot, current_pnl=-2.5)
        check_safety_and_goals(bot, current_pnl=-2.6)

        warning_events = [
            call for call in mock_evt.call_args_list if call.args[1] == "DAILY_DRAWDOWN_WARNING"
        ]
        self.assertEqual(len(warning_events), 1)


if __name__ == "__main__":
    unittest.main()

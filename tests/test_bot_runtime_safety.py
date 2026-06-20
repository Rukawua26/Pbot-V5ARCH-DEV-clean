import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class BotRuntimeSafetyTest(unittest.TestCase):
    def _bot(self):
        return SimpleNamespace(
            daily_initial_balance=100.0,
            balance=100.0,
            peak_pnl=0.0,
            current_target=5.0,
            circuit_breaker_active=False,
            log=MagicMock(),
        )

    @patch("core.bot_runtime_safety.Config.DAILY_TRAILING_STOP", 3.0)
    @patch("core.bot_runtime_safety.activate_runtime_protection")
    def test_trailing_stop_activates_runtime_protection(self, protect):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()
        bot.peak_pnl = 6.0

        self.assertFalse(check_safety_and_goals(bot, current_pnl=2.9))
        protect.assert_called_once()
        self.assertEqual(protect.call_args.kwargs["reason"], "DAILY_TRAILING_STOP_HIT")

    @patch("core.bot_runtime_safety.Config.DAILY_LOSS_LIMIT", 3.0)
    @patch("core.bot_runtime_safety.activate_runtime_protection")
    def test_daily_loss_limit_activates_defensive_mode(self, protect):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()

        self.assertFalse(check_safety_and_goals(bot, current_pnl=-3.0))
        protect.assert_called_once()
        self.assertTrue(protect.call_args.kwargs["pause"])
        self.assertEqual(protect.call_args.kwargs["reason"], "DAILY_LOSS_LIMIT_REACHED")

    @patch("core.bot_runtime_safety.Config.DAILY_GOALS", [5.0, 10.0])
    def test_goal_advances_current_target(self):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()

        self.assertTrue(check_safety_and_goals(bot, current_pnl=5.0))
        self.assertEqual(bot.current_target, 10.0)
        self.assertFalse(bot.circuit_breaker_active)

    @patch("core.bot_runtime_safety.Config.DAILY_GOALS", [5.0])
    def test_final_goal_activates_circuit_breaker(self):
        from core.bot_runtime_safety import check_safety_and_goals

        bot = self._bot()

        self.assertTrue(check_safety_and_goals(bot, current_pnl=5.0))
        self.assertTrue(bot.circuit_breaker_active)


if __name__ == "__main__":
    unittest.main()

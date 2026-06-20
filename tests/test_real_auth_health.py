import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class RealAuthHealthTest(unittest.TestCase):
    def _bot(self, fetch_balance):
        return SimpleNamespace(
            execution=SimpleNamespace(fetch_balance=fetch_balance),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            _last_real_auth_healthcheck_mono=0.0,
            log=MagicMock(),
        )

    @patch("core.real_auth_health.Config.PAPER_MODE", True)
    def test_skips_in_paper_mode(self):
        from core.real_auth_health import maybe_check_real_auth

        fetch_balance = MagicMock(side_effect=RuntimeError("should not call"))
        bot = self._bot(fetch_balance)

        self.assertTrue(maybe_check_real_auth(bot, now_mono=10.0))
        fetch_balance.assert_not_called()

    @patch("core.real_auth_health.Config.REAL_AUTH_HEALTHCHECK_INTERVAL_SECONDS", 60, create=True)
    @patch("core.real_auth_health.Config.PAPER_MODE", False)
    def test_success_records_last_check_and_respects_interval(self):
        from core.real_auth_health import maybe_check_real_auth

        fetch_balance = MagicMock(return_value={"total": {"USDT": 1.0}})
        bot = self._bot(fetch_balance)

        self.assertTrue(maybe_check_real_auth(bot, now_mono=100.0))
        self.assertTrue(maybe_check_real_auth(bot, now_mono=120.0))
        self.assertEqual(fetch_balance.call_count, 1)

    @patch("core.real_auth_health.send_telegram_msg")
    @patch("core.real_auth_health.append_execution_event")
    @patch("core.real_auth_health.Config.PAPER_MODE", False)
    def test_auth_like_failure_activates_halt(self, append_event, send_msg):
        from core.real_auth_health import maybe_check_real_auth

        bot = self._bot(MagicMock(side_effect=RuntimeError("Invalid API-key, IP, or permissions")))

        self.assertFalse(maybe_check_real_auth(bot, now_mono=100.0))
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        append_event.assert_called_once()
        send_msg.assert_called_once()

    @patch("core.real_auth_health.Config.PAPER_MODE", False)
    def test_transient_failure_does_not_halt(self):
        from core.real_auth_health import maybe_check_real_auth

        bot = self._bot(MagicMock(side_effect=RuntimeError("temporary 502")))

        self.assertTrue(maybe_check_real_auth(bot, now_mono=100.0))
        self.assertFalse(bot.halt_system_active)


if __name__ == "__main__":
    unittest.main()

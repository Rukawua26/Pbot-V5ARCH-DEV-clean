import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.ws_reconciliation import handle_ws_reconnected


class WebSocketReconciliationTests(unittest.TestCase):
    def _bot(self):
        return SimpleNamespace(
            log=MagicMock(),
            ws_reconciliation_in_progress=False,
            _last_ws_reconcile_mono=0.0,
        )

    @patch("core.ws_reconciliation._check_ws_reconcile_timeout")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", True)
    def test_paper_mode_records_and_skips_reconciliation(self, append_event, _timeout):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=1)

        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONNECTED", events)
        self.assertIn("WS_RECONCILE_SKIPPED", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)

    @patch("core.ws_reconciliation._check_ws_reconcile_timeout")
    @patch("core.ws_reconciliation.reconcile_bootstrap_state")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_reconciles_and_clears_in_progress(self, append_event, reconcile, _timeout):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=2)

        reconcile.assert_called_once_with(bot)
        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONCILE_STARTED", events)
        self.assertIn("WS_RECONCILE_OK", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)

    @patch("core.ws_reconciliation._check_ws_reconcile_timeout")
    @patch("core.ws_reconciliation.reconcile_bootstrap_state")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_debounces_reconciliation(self, _append_event, reconcile, _timeout):
        bot = self._bot()
        bot._last_ws_reconcile_mono = 1000.0

        with patch("core.ws_reconciliation.time.monotonic", return_value=1005.0):
            handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=3)

        reconcile.assert_not_called()

    @patch("core.ws_reconciliation._check_ws_reconcile_timeout")
    @patch("core.ws_reconciliation.activate_runtime_protection")
    @patch("core.ws_reconciliation.reconcile_bootstrap_state", side_effect=RuntimeError("boom"))
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_halts_on_reconciliation_exception(
        self,
        append_event,
        _reconcile,
        activate_protection,
        _timeout,
    ):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=4)

        activate_protection.assert_called_once()
        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONCILE_HALT", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)

    @patch("core.ws_reconciliation.send_telegram_msg")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.WS_RECONCILE_TIMEOUT_SECONDS", 0.01)
    def test_timeout_daemon_alerts_when_flag_stays_active(self, append_event, send_telegram):
        bot = self._bot()
        bot.ws_reconciliation_in_progress = True

        # Simulate the loop: first iteration sees flag True, elapsed exceeds timeout
        monotonic_values = iter([0.0, 999.0])

        with (
            patch.object(bot, "log", MagicMock()),
            patch(
                "core.ws_reconciliation.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ),
            patch("core.ws_reconciliation.time.sleep"),
        ):
            from core.ws_reconciliation import _check_ws_reconcile_timeout

            _check_ws_reconcile_timeout(bot)

        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONCILE_TIMEOUT_ALERT", events)
        send_telegram.assert_called_once()


if __name__ == "__main__":
    unittest.main()

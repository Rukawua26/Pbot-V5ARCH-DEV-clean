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

    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", True)
    def test_paper_mode_records_and_skips_reconciliation(self, append_event):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=1)

        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONNECTED", events)
        self.assertIn("WS_RECONCILE_SKIPPED", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)

    @patch("core.ws_reconciliation.reconcile_bootstrap_state")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_reconciles_and_clears_in_progress(self, append_event, reconcile):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=2)

        reconcile.assert_called_once_with(bot)
        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONCILE_STARTED", events)
        self.assertIn("WS_RECONCILE_OK", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)

    @patch("core.ws_reconciliation.reconcile_bootstrap_state")
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_debounces_reconciliation(self, _append_event, reconcile):
        bot = self._bot()
        bot._last_ws_reconcile_mono = 1000.0

        with patch("core.ws_reconciliation.time.monotonic", return_value=1005.0):
            handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=3)

        reconcile.assert_not_called()

    @patch("core.ws_reconciliation.activate_runtime_protection")
    @patch("core.ws_reconciliation.reconcile_bootstrap_state", side_effect=RuntimeError("boom"))
    @patch("core.ws_reconciliation.append_execution_event")
    @patch("core.ws_reconciliation.Config.PAPER_MODE", False)
    def test_real_mode_halts_on_reconciliation_exception(
        self,
        append_event,
        _reconcile,
        activate_protection,
    ):
        bot = self._bot()

        handle_ws_reconnected(bot, source="ticker_ws", reconnect_count=4)

        activate_protection.assert_called_once()
        events = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("WS_RECONCILE_HALT", events)
        self.assertFalse(bot.ws_reconciliation_in_progress)


if __name__ == "__main__":
    unittest.main()

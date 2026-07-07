import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class GuardianHardSlSyncTest(unittest.TestCase):
    def _bot(self, place_hard_sl):
        return SimpleNamespace(
            db_lock=RLock(),
            execution=SimpleNamespace(
                place_hard_sl=place_hard_sl,
                cancel_order=MagicMock(),
            ),
            brain=SimpleNamespace(save_active_trade_state=MagicMock()),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            log=MagicMock(),
        )

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.append_execution_event")
    def test_failed_real_hard_sl_amend_halts_and_persists(self, append_event):
        from core.bot_guardian import _sync_tightened_hard_sl

        trade = {
            "side": "BUY",
            "sl": 101.0,
            "amount": 0.2,
            "sl_exchange_order_id": "old-sl",
            "sl_client_order_id": "SL_BASE",
        }
        bot = self._bot(MagicMock(return_value=None))

        _sync_tightened_hard_sl(bot, "BTC/USDT", trade, previous_sl=100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(trade["status"], "HARD_SL_AMEND_FAILED")
        bot.brain.save_active_trade_state.assert_called_once_with("BTC/USDT", trade)
        append_event.assert_called_once()

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.append_execution_event")
    def test_successful_real_hard_sl_amend_replaces_order(self, append_event):
        from core.bot_guardian import _sync_tightened_hard_sl

        trade = {
            "side": "SELL",
            "sl": 99.0,
            "amount": 0.2,
            "sl_exchange_order_id": "old-sl",
            "sl_client_order_id": "SL_BASE",
            "sl_amend_count": 1,
        }
        bot = self._bot(MagicMock(return_value={"id": "new-sl"}))

        _sync_tightened_hard_sl(bot, "BTC/USDT", trade, previous_sl=100.0)

        self.assertFalse(bot.halt_system_active)
        self.assertEqual(trade["sl_exchange_order_id"], "new-sl")
        self.assertEqual(trade["hard_sl_price"], 99.0)
        self.assertEqual(trade["sl_amend_count"], 2)
        bot.execution.cancel_order.assert_called_once_with("BTC/USDT", "old-sl")
        bot.brain.save_active_trade_state.assert_called_once_with("BTC/USDT", trade)
        append_event.assert_called_once()

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.append_execution_event")
    def test_ambiguous_cancel_after_hard_sl_amend_halts(self, append_event):
        from core.bot_guardian import _sync_tightened_hard_sl

        trade = {
            "side": "BUY",
            "sl": 101.0,
            "amount": 0.2,
            "sl_exchange_order_id": "old-sl",
            "sl_client_order_id": "SL_BASE",
        }
        bot = self._bot(MagicMock(return_value={"id": "new-sl"}))
        bot.execution.cancel_order.side_effect = RuntimeError("cancel ambiguous")

        _sync_tightened_hard_sl(bot, "BTC/USDT", trade, previous_sl=100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(trade["status"], "HARD_SL_AMEND_CANCEL_AMBIGUOUS")
        self.assertEqual(trade["sl_exchange_order_id"], "new-sl")
        bot.brain.save_active_trade_state.assert_called_once_with("BTC/USDT", trade)
        append_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()

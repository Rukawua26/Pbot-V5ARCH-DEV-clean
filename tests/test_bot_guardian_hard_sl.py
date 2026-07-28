import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _valid_hard_sl_ack(symbol: str, sl_side: str, amount: float, order_id: str = "new-sl") -> dict:
    """ACK estructuralmente válido para hard_sl_ack_looks_valid."""
    return {
        "id": order_id,
        "symbol": symbol,
        "type": "STOP_MARKET",
        "side": sl_side.lower(),
        "amount": amount,
        "status": "open",
        "info": {"reduceOnly": True},
    }


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
        bot = self._bot(MagicMock(return_value=_valid_hard_sl_ack("BTC/USDT", "buy", 0.2)))

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
        bot = self._bot(MagicMock(return_value=_valid_hard_sl_ack("BTC/USDT", "sell", 0.2)))
        bot.execution.cancel_order.side_effect = RuntimeError("cancel ambiguous")

        _sync_tightened_hard_sl(bot, "BTC/USDT", trade, previous_sl=100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(trade["status"], "HARD_SL_AMEND_CANCEL_AMBIGUOUS")
        self.assertEqual(trade["sl_exchange_order_id"], "new-sl")
        bot.brain.save_active_trade_state.assert_called_once_with("BTC/USDT", trade)
        append_event.assert_called_once()

    @patch("core.bot_guardian.Config.PAPER_MODE", False)
    @patch("core.bot_guardian.append_execution_event")
    def test_invalid_hard_sl_ack_halts_as_amend_failed(self, append_event):
        """ACK truthy pero inválido (side opuesto al esperado) debe HALT, no aceptarse."""
        from core.bot_guardian import _sync_tightened_hard_sl

        trade = {
            "side": "BUY",
            "sl": 101.0,
            "amount": 0.2,
            "sl_exchange_order_id": "old-sl",
            "sl_client_order_id": "SL_BASE",
        }
        # ACK con side 'buy' en vez de 'sell' esperado: HARD_SL_ACK_SIDE_MISMATCH
        bad_ack = _valid_hard_sl_ack("BTC/USDT", "buy", 0.2)
        bot = self._bot(MagicMock(return_value=bad_ack))

        _sync_tightened_hard_sl(bot, "BTC/USDT", trade, previous_sl=100.0)

        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertEqual(trade["status"], "HARD_SL_AMEND_FAILED")
        # No se debe haber cancelado el SL viejo si el nuevo no es válido
        bot.execution.cancel_order.assert_not_called()
        append_event.assert_called_once()
        call_args = append_event.call_args[0]
        self.assertEqual(call_args[1], "HARD_SL_AMEND_FAILED_HALT")
        payload = call_args[2]
        self.assertIn("SIDE_MISMATCH", payload["ack_reason"])


if __name__ == "__main__":
    unittest.main()

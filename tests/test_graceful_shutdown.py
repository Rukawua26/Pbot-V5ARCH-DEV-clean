import threading
import unittest
from types import SimpleNamespace

from core.bot_shutdown import _shutdown_sequence, request_graceful_shutdown
from core.trade_manager import execute_order


class _FakeBrain:
    def save_active_trade_state(self, _symbol, _trade):
        return True


class _FakeExecution:
    def __init__(self):
        self.canceled = []

    def fetch_open_orders(self, _symbol=None):
        return [
            {"id": "1", "symbol": "BTC/USDT", "type": "limit", "info": {}},
            {
                "id": "2",
                "symbol": "BTC/USDT",
                "type": "STOP_MARKET",
                "info": {"type": "STOP_MARKET"},
            },
        ]

    def cancel_order(self, symbol, order_id):
        self.canceled.append((symbol, order_id))
        return {"id": order_id, "status": "canceled"}


class GracefulShutdownTest(unittest.TestCase):
    def _build_bot(self):
        bot = SimpleNamespace()
        bot.stop_requested = False
        bot.shutdown_in_progress = False
        bot.halt_system_active = False
        bot.integrity_lock_active = False
        bot.is_running = True
        bot.shutdown_complete = threading.Event()
        bot._shadow_logger = SimpleNamespace(stop=lambda: None)
        bot.ui = SimpleNamespace(stop=lambda: None)
        bot.ws_manager = SimpleNamespace(stop=lambda: None)
        bot.execution = _FakeExecution()
        bot.active_trades = {
            "BTC/USDT": {
                "status": "PARTIAL_FILL_PENDING",
                "partial_fill_pending": True,
                "remaining_amount": 0.3,
            }
        }
        bot.lock = threading.RLock()
        bot.db_lock = threading.RLock()
        bot.brain = _FakeBrain()
        bot.logs = []
        bot.log = lambda msg: bot.logs.append(msg)
        bot.sync_wallet = lambda: True
        bot.save_cache = lambda: True
        return bot

    def test_shutdown_sequence_cancels_only_non_protective_orders(self):
        bot = self._build_bot()
        logger = SimpleNamespace(warning=lambda *_a, **_k: None)

        _shutdown_sequence(bot, reason="TEST", logger=logger)

        self.assertTrue(bot.shutdown_complete.is_set())
        self.assertFalse(bot.is_running)
        self.assertEqual(bot.execution.canceled, [("BTC/USDT", "1")])
        trade = bot.active_trades["BTC/USDT"]
        self.assertEqual(trade.get("status"), "CLOSED")
        self.assertEqual(trade.get("entry_order_status"), "CLOSED")
        self.assertEqual(float(trade.get("remaining_amount") or 0.0), 0.0)

    def test_trade_manager_rejects_new_entries_during_shutdown(self):
        bot = SimpleNamespace(stop_requested=True, shutdown_in_progress=True, log=lambda *_a: None)
        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=True,
            context={},
        )
        self.assertEqual(result, "SHUTDOWN_IN_PROGRESS")

    def test_request_graceful_shutdown_creates_event_when_missing(self):
        bot = self._build_bot()
        del bot.shutdown_complete
        logger = SimpleNamespace(warning=lambda *_a, **_k: None)

        request_graceful_shutdown(bot, reason="TEST", logger=logger)
        bot._shutdown_thread.join(timeout=2.0)

        self.assertTrue(bot.shutdown_in_progress)
        self.assertTrue(bot.shutdown_complete.is_set())


if __name__ == "__main__":
    unittest.main()

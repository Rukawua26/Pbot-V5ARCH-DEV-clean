import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.learning import Brain


class ActiveTradeStoreTest(unittest.TestCase):
    def test_save_load_delete_active_trade_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))
            open_time = datetime(2026, 1, 2, 3, 4, 5)

            saved = brain.save_active_trade_state(
                "BTC/USDT",
                {
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "open_time": open_time,
                    "entry_price": 100.0,
                },
            )

            self.assertTrue(saved)
            loaded = brain.load_active_trade_states()
            self.assertEqual(loaded["BTC/USDT"]["open_time"], open_time)
            self.assertEqual(loaded["BTC/USDT"]["entry_price"], 100.0)

            brain.delete_active_trade_state("BTC/USDT")

            self.assertEqual(brain.load_active_trade_states(), {})

    def test_can_persist_two_hedge_legs_for_same_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))

            self.assertTrue(
                brain.save_active_trade_state(
                    "BTC/USDT|BUY",
                    {"trade_key": "BTC/USDT|BUY", "symbol": "BTC/USDT", "side": "BUY"},
                )
            )
            self.assertTrue(
                brain.save_active_trade_state(
                    "BTC/USDT|SELL",
                    {"trade_key": "BTC/USDT|SELL", "symbol": "BTC/USDT", "side": "SELL"},
                )
            )

            loaded = brain.load_active_trade_states()

            self.assertEqual(set(loaded), {"BTC/USDT|BUY", "BTC/USDT|SELL"})
            self.assertEqual(loaded["BTC/USDT|BUY"]["symbol"], "BTC/USDT")
            self.assertEqual(loaded["BTC/USDT|SELL"]["side"], "SELL")

            brain.delete_active_trade_state("BTC/USDT|BUY")
            loaded = brain.load_active_trade_states()

            self.assertEqual(set(loaded), {"BTC/USDT|SELL"})


if __name__ == "__main__":
    unittest.main()

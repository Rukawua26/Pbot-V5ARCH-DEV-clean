import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.bot_signals import run_signal_scan_cycle


class BotSignalScanCycleTest(unittest.TestCase):
    def test_timeout_result_does_not_trigger_latency_quarantine(self):
        bot = SimpleNamespace(
            latency_quarantine={},
            market_breadth={},
            log=MagicMock(),
            update_radar=MagicMock(),
        )
        top_triage = [{"symbol": "BTC/USDT"}]
        results = {"BTC/USDT": {"data": None, "elapsed": -1, "error": "TIMEOUT"}}
        signal_stats = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "REAL": 0, "SHADOW": 0, "VETO": 0}

        run_signal_scan_cycle(bot, top_triage, results, signal_stats, pnl_real_hoy=0.0)

        self.assertEqual(bot.latency_quarantine, {})
        self.assertFalse(
            any("VETO LATENCIA" in str(call.args[0]) for call in bot.log.call_args_list)
        )
        bot.update_radar.assert_called_once()

    def test_no_data_result_does_not_trigger_latency_quarantine(self):
        bot = SimpleNamespace(
            latency_quarantine={},
            market_breadth={},
            log=MagicMock(),
            update_radar=MagicMock(),
        )
        top_triage = [{"symbol": "ETH/USDT"}]
        results = {"ETH/USDT": {"data": (None, None), "elapsed": 120, "error": None}}
        signal_stats = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "REAL": 0, "SHADOW": 0, "VETO": 0}

        run_signal_scan_cycle(bot, top_triage, results, signal_stats, pnl_real_hoy=0.0)

        self.assertEqual(bot.latency_quarantine, {})
        self.assertFalse(
            any("VETO LATENCIA" in str(call.args[0]) for call in bot.log.call_args_list)
        )
        bot.update_radar.assert_called_once()


if __name__ == "__main__":
    unittest.main()

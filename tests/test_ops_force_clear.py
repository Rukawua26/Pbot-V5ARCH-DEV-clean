import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.commands.ops import (
    _handle_misc_commands,
    _handle_training_and_maintenance_commands,
)


class OpsForceClearTest(unittest.TestCase):
    @patch("core.commands.ops.send_telegram_msg")
    def test_pipeline_reports_hmm_and_ws_state(self, mocked_tg):
        bot = SimpleNamespace(
            market_regime="BULL_TREND",
            market_regime_source="HMM",
            market_regime_confidence=0.87,
            market_btc_price=65000.0,
            market_btc_price_source="WS_TICKER",
            market_btc_price_ts=10.0,
            hmm_markov_snapshot={
                "ts": datetime.now(UTC).isoformat(),
                "state": "RANGE",
                "bullish_breakout_prob": 82.0,
                "bearish_reversal_prob": 12.0,
                "range_prob": 6.0,
            },
            markov_decision_stats={
                "range_breakout_allowed": 3,
                "range_standard_penalty": 5,
                "range_stagnant_veto": 1,
            },
        )

        with patch("core.commands.ops.monotonic_now", return_value=12.5):
            handled = _handle_misc_commands(bot, "/pipeline")

        self.assertTrue(handled)
        mocked_tg.assert_called_once()
        msg = mocked_tg.call_args[0][0]
        self.assertIn("PIPELINE STATUS", msg)
        self.assertIn("BULL_TREND", msg)
        self.assertIn("87.0%", msg)
        self.assertIn("WS_TICKER", msg)
        self.assertIn("2.5s", msg)
        self.assertIn("Markov", msg)
        self.assertIn("82.0%", msg)
        self.assertIn("Range breakout: 3", msg)

    @patch("core.commands.ops.send_telegram_msg")
    def test_sre_intent_reports_ratio(self, mocked_tg):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "execution_events.jsonl"
            now = datetime.now(UTC).isoformat()
            rows = [
                {"ts": now, "event": "ENTRY_ORDER_ACK", "payload": {}},
                {"ts": now, "event": "ENTRY_ORDER_ACK", "payload": {}},
                {"ts": now, "event": "INTENT_EXPIRED", "payload": {}},
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

            bot = SimpleNamespace(
                weight_tracker=SimpleNamespace(
                    get_status=lambda: {
                        "current_weight": 120,
                        "limit": 2400,
                        "usage_pct": 5.0,
                    }
                )
            )

            with patch("core.commands.ops.Path", side_effect=lambda _p: log_path):
                handled = _handle_misc_commands(bot, "/sre_intent")

            self.assertTrue(handled)
            mocked_tg.assert_called_once()
            msg = mocked_tg.call_args[0][0]
            self.assertIn("RATIO=50.00%", msg)
            self.assertIn("120/2400", msg)

    @patch("core.commands.ops.send_telegram_msg")
    def test_force_clear_removes_state_when_no_exchange_evidence(self, mocked_tg):
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "status": "PENDING_SEND",
                    "simulated_real": True,
                    "entry_client_order_id": "sai-v118-abc",
                }
            },
            execution=SimpleNamespace(
                fetch_open_orders=lambda _symbol: [],
                fetch_order_by_client_id=lambda _symbol, _coid: None,
                fetch_positions=lambda: [],
            ),
            brain=SimpleNamespace(delete_active_trade_state=MagicMock()),
        )

        handled = _handle_training_and_maintenance_commands(bot, "/force_clear BTC/USDT")

        self.assertTrue(handled)
        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        mocked_tg.assert_called()

    @patch("core.commands.ops.send_telegram_msg")
    def test_force_clear_does_not_remove_state_when_exchange_has_position(self, mocked_tg):
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "status": "PENDING_SEND",
                    "entry_client_order_id": "sai-v118-abc",
                }
            },
            execution=SimpleNamespace(
                fetch_open_orders=lambda _symbol: [],
                fetch_order_by_client_id=lambda _symbol, _coid: None,
                fetch_positions=lambda: [{"symbol": "BTC/USDT:USDT", "contracts": 0.1}],
            ),
            brain=SimpleNamespace(delete_active_trade_state=MagicMock()),
        )

        handled = _handle_training_and_maintenance_commands(bot, "/force_clear BTC/USDT")

        self.assertTrue(handled)
        self.assertIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_not_called()
        mocked_tg.assert_called()

    @patch("core.commands.ops.Config.PAPER_MODE", True)
    @patch("core.commands.ops.send_telegram_msg")
    def test_force_clear_quarantines_non_simulated_state_in_paper(self, mocked_tg):
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "is_shadow": False,
                    "simulated_real": False,
                }
            },
            execution=SimpleNamespace(fetch_positions=MagicMock()),
            brain=SimpleNamespace(delete_active_trade_state=MagicMock()),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
        )

        self.assertTrue(_handle_training_and_maintenance_commands(bot, "/force_clear BTC/USDT"))

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertTrue(bot.halt_system_active)
        bot.brain.delete_active_trade_state.assert_not_called()
        mocked_tg.assert_called_once()


if __name__ == "__main__":
    unittest.main()

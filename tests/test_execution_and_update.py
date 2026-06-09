import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from core.signals.execution import _execute_and_update_symbol


def _make_bot(exec_result="OK_SHADOW"):
    df = pd.DataFrame({"close": [100.0] * 10})
    bot = SimpleNamespace(
        execute_order=MagicMock(return_value=exec_result),
        update_radar=MagicMock(),
        log=MagicMock(),
        lock=MagicMock(),
        active_trades={},
        scanner_history=[],
    )
    bot.lock.__enter__ = MagicMock(return_value=None)
    bot.lock.__exit__ = MagicMock(return_value=None)
    return bot, df


class TestExecuteAndUpdateSymbol(unittest.TestCase):
    def setUp(self):
        self.ctx = {"atr": 1.5}
        self.kw = dict(
            symbol_raw="BTC/USDT",
            symbol="BTC/USDT",
            audit_signal="BUY",
            prob_final=75.0,
            audit_verdict="STRONG_BUY",
            should_execute=True,
            is_shadow_exec=True,
            df_main=pd.DataFrame({"close": [100.0] * 10}),
            ctx=self.ctx,
            ob_status="⚪",
            votos={"MT": 1},
            decision={"signal": "BUY"},
            elapsed=0.0,
        )

    def test_should_not_execute_skips_order(self):
        bot = SimpleNamespace(update_radar=MagicMock(), log=MagicMock())
        self.kw["should_execute"] = False
        self.kw["votos"] = None
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.update_radar.assert_called_once()
        bot.log.assert_called_once()
        self.assertFalse(hasattr(bot, "execute_order"))

    def test_ok_shadow_path(self):
        bot, _ = _make_bot("OK_SHADOW")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.execute_order.assert_called_once()
        bot.log.assert_called_once()
        bot.update_radar.assert_called_once()

    def test_ok_real_path(self):
        bot, _ = _make_bot("OK")
        self.kw["is_shadow_exec"] = False
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.log.assert_called_once()
        bot.update_radar.assert_called_once()

    def test_degraded_path(self):
        bot, _ = _make_bot("OK_DEGRADED: LOW_CONFIDENCE")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.log.assert_called_once()

    def test_veto_error_path(self):
        bot, _ = _make_bot("BOT_PAUSED: paused")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.log.assert_any_call("❌ FALLO EJECUCIÓN BTC/USDT: BOT_PAUSED: paused")

    def test_cooldown_path(self):
        bot, _ = _make_bot("COOLDOWN")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.update_radar.assert_called_once()

    def test_already_active_path(self):
        bot, _ = _make_bot("ALREADY_ACTIVE")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.update_radar.assert_called_once()

    def test_active_trade_overwrites_verdict(self):
        bot, _ = _make_bot("OK_REAL")
        bot.active_trades = {"BTC/USDT": {"entry": 100.0}}
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.update_radar.assert_called_once()

    def test_scanner_history_updated(self):
        bot, _ = _make_bot("BOT_PAUSED: paused")
        bot.scanner_history = [{"symbol": "BTC/USDT", "result": "", "ia_real": "", "ia_shadow": ""}]
        _execute_and_update_symbol(bot=bot, **self.kw)
        self.assertIn("VETO", bot.scanner_history[0]["result"])

    def test_unrecognized_veto_preserves_error(self):
        bot, _ = _make_bot("UNKNOWN_ERROR: something")
        _execute_and_update_symbol(bot=bot, **self.kw)
        bot.update_radar.assert_called_once()
        bot.log.assert_any_call("❌ FALLO EJECUCIÓN BTC/USDT: UNKNOWN_ERROR: something")


if __name__ == "__main__":
    unittest.main()

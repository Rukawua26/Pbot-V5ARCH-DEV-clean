import threading
import unittest
from unittest.mock import MagicMock, patch

from core import bot_app


class BotBootstrapTest(unittest.TestCase):
    @patch("core.bot_app.asyncio.new_event_loop")
    @patch("core.bot_app.uvloop", None)
    def test_new_runtime_event_loop_falls_back_to_asyncio(self, mocked_new_loop):
        expected_loop = object()
        mocked_new_loop.return_value = expected_loop

        self.assertIs(bot_app._new_runtime_event_loop(), expected_loop)
        mocked_new_loop.assert_called_once_with()

    def test_new_runtime_event_loop_prefers_uvloop(self):
        expected_loop = object()
        mocked_uvloop = MagicMock()
        mocked_uvloop.new_event_loop.return_value = expected_loop

        with patch("core.bot_app.uvloop", mocked_uvloop):
            self.assertIs(bot_app._new_runtime_event_loop(), expected_loop)

        mocked_uvloop.new_event_loop.assert_called_once_with()

    @patch("core.bot_app.asyncio.new_event_loop")
    def test_new_runtime_event_loop_falls_back_when_uvloop_construction_fails(
        self, mocked_new_loop
    ):
        expected_loop = object()
        mocked_new_loop.return_value = expected_loop
        mocked_uvloop = MagicMock()
        mocked_uvloop.new_event_loop.side_effect = RuntimeError("unsupported")

        with patch("core.bot_app.uvloop", mocked_uvloop):
            self.assertIs(bot_app._new_runtime_event_loop(), expected_loop)

        mocked_new_loop.assert_called_once_with()

    def test_bind_main_loop_starts_running_loop_thread(self):
        bot = bot_app.Bot.__new__(bot_app.Bot)
        bot.main_loop = None
        bot._main_loop_ready = threading.Event()
        bot._main_loop_thread = None

        bot_app.Bot._bind_main_loop_or_abort(bot)

        try:
            self.assertIsNotNone(bot.main_loop)
            self.assertTrue(bot.main_loop.is_running())
            self.assertTrue(bot._main_loop_thread.is_alive())
        finally:
            bot.main_loop.call_soon_threadsafe(bot.main_loop.stop)
            bot._main_loop_thread.join(timeout=2.0)

    @patch.object(bot_app.Bot, "_init_models_and_startup_tasks")
    @patch.object(bot_app.Bot, "_init_realtime_and_monitoring")
    @patch.object(bot_app.Bot, "_warmup_hmm_regime")
    @patch.object(bot_app.Bot, "_init_runtime_state")
    @patch.object(bot_app.Bot, "_init_core_services_and_engines")
    @patch.object(bot_app.Bot, "_bind_main_loop_or_abort")
    @patch("core.bot_app.Brain")
    @patch("core.bot_app.UI")
    def test_bot_constructor_wires_bootstrap_sequence_without_services(
        self,
        mocked_ui,
        mocked_brain,
        mocked_bind_loop,
        mocked_core,
        mocked_runtime,
        mocked_warmup,
        mocked_realtime,
        mocked_models,
    ):
        bot = bot_app.Bot()

        self.assertTrue(bot.is_running)
        self.assertIs(bot.ui, mocked_ui.return_value)
        self.assertIs(bot.brain, mocked_brain.return_value)
        mocked_bind_loop.assert_called_once_with()
        mocked_core.assert_called_once_with()
        mocked_runtime.assert_called_once_with()
        mocked_warmup.assert_called_once_with()
        mocked_realtime.assert_called_once_with()
        mocked_models.assert_called_once_with()

    @patch.object(bot_app.Config, "PAPER_MODE", False)
    @patch.object(bot_app.Config, "ALLOW_REAL_TRADING", False)
    def test_real_mode_guardrails_reject_missing_explicit_allow(self):
        with self.assertRaisesRegex(RuntimeError, "ALLOW_REAL_TRADING=false"):
            bot_app._check_real_mode_guardrails()

    @patch.object(bot_app.Config, "PAPER_MODE", True)
    def test_real_mode_guardrails_allow_paper_mode(self):
        bot_app._check_real_mode_guardrails()

    @patch("core.bot_app.evaluate_safety_and_goals", return_value="safety-ok")
    @patch("core.bot_app.run_check_instinctive_safety", return_value="instinct-ok")
    @patch("core.bot_app.connect_to_binance", return_value="connected")
    def test_delegated_bot_methods_preserve_self_args_and_kwargs(
        self,
        mocked_connect,
        mocked_instinctive_safety,
        mocked_safety_goals,
    ):
        bot = bot_app.Bot.__new__(bot_app.Bot)

        self.assertEqual(bot.connect(), "connected")
        self.assertEqual(bot.check_instinctive_safety("BTC/USDT", {"risk": "low"}), "instinct-ok")
        self.assertEqual(bot.check_safety_and_goals(current_pnl=1.25), "safety-ok")

        mocked_connect.assert_called_once_with(bot)
        mocked_instinctive_safety.assert_called_once_with(bot, "BTC/USDT", {"risk": "low"})
        mocked_safety_goals.assert_called_once_with(bot, current_pnl=1.25)

    @patch("core.bot_app.signal.signal")
    @patch("core.bot_app.Bot")
    @patch("core.bot_app._check_real_mode_guardrails")
    @patch.object(bot_app.Config, "validate", return_value=[])
    @patch.object(bot_app.Config, "env_warnings", return_value=[])
    @patch("core.bot_app.acquire_single_instance_lock", return_value=True)
    def test_run_entrypoint_starts_bot_when_bootstrap_is_valid(
        self,
        mocked_lock,
        mocked_env_warnings,
        mocked_validate,
        mocked_guardrails,
        mocked_bot_cls,
        mocked_signal,
    ):
        loop = MagicMock()
        loop.is_closed.return_value = False
        loop.is_running.return_value = True
        bot = MagicMock()
        bot.main_loop = loop
        bot.shutdown_in_progress = False
        mocked_bot_cls.return_value = bot

        bot_app.run_entrypoint()

        mocked_lock.assert_called_once()
        mocked_env_warnings.assert_called_once_with()
        mocked_validate.assert_called_once_with()
        mocked_guardrails.assert_called_once_with()
        bot.run.assert_called_once_with()
        self.assertGreaterEqual(mocked_signal.call_count, 1)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_runtime import run_bot_runtime_loop, run_initial_load


class BotRuntimeStartupTest(unittest.TestCase):
    @patch("core.bot_runtime.reconcile_bootstrap_state")
    def test_initial_load_preserves_bootstrap_error_for_main_thread(self, _reconcile):
        error = RuntimeError("Credenciales/permisos Binance inválidos")
        bot = SimpleNamespace(
            connect=MagicMock(side_effect=error),
            acquire_targets=MagicMock(),
            _load_ai_restrictions=MagicMock(),
            log=MagicMock(),
            is_running=True,
            init_complete=MagicMock(),
        )

        run_initial_load(bot, dashboard_module=None)

        self.assertIs(bot.startup_error, error)
        self.assertFalse(bot.is_running)
        bot.init_complete.set.assert_called_once()

    @patch("core.bot_runtime.consume_command_file")
    def test_runtime_loop_consumes_dashboard_ipc_commands(self, consume_command_file):
        bot = SimpleNamespace(
            is_running=True,
            ui=SimpleNamespace(
                start=MagicMock(),
                update=MagicMock(),
                render=MagicMock(),
                stop=MagicMock(),
            ),
            _initial_load=MagicMock(),
            _collect_telemetry=MagicMock(return_value={}),
            ml_monitor=None,
            active_trades={},
            recent_closed_trades=[],
            scanner_history=[],
            balance=0.0,
        )

        def stop_loop(_seconds):
            bot.is_running = False

        with (
            patch("core.bot_runtime.threading.Thread") as thread_cls,
            patch("core.bot_runtime.time.sleep", side_effect=stop_loop),
        ):
            thread_cls.return_value.start = MagicMock()
            run_bot_runtime_loop(
                bot,
                dashboard_module=None,
                logger=MagicMock(),
                shadow_logger=MagicMock(),
            )

        consume_command_file.assert_called_with(bot)


if __name__ == "__main__":
    unittest.main()

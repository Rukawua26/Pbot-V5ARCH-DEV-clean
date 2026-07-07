import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_models_startup import init_models_and_startup_tasks


class ModelStartupSecurityTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=False)
    @patch("core.bot_models_startup.threading.Thread")
    @patch("core.bot_models_startup.joblib.load")
    @patch("core.bot_models_startup.os.path.exists")
    def test_lstm_legacy_load_requires_explicit_opt_in(self, exists, joblib_load, thread_cls):
        exists.side_effect = lambda path: path in {"models/lstm_model.h5", "models/scaler.pkl"}
        thread_cls.return_value.start = MagicMock()
        tf_module = SimpleNamespace(
            keras=SimpleNamespace(models=SimpleNamespace(load_model=MagicMock()))
        )
        bot = SimpleNamespace(
            log=MagicMock(),
            _websocket_monitor=MagicMock(),
            ghost_model_type="OFF",
            bootstrap_heuristic_mode=False,
            ai_status_msg="",
            brain=SimpleNamespace(
                cleanup_stale_snapshots=MagicMock(return_value=0),
                _get_conn=MagicMock(side_effect=RuntimeError("no db")),
            ),
            handle_command=MagicMock(),
        )

        init_models_and_startup_tasks(bot, None, None, tf_module)

        tf_module.keras.models.load_model.assert_not_called()
        joblib_load.assert_not_called()
        self.assertTrue(bot.bootstrap_heuristic_mode)
        self.assertEqual(bot.ai_status_msg, "BOOTSTRAP_HEURISTIC")


if __name__ == "__main__":
    unittest.main()

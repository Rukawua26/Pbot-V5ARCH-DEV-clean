import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_ml_runtime import init_ml_monitoring
from core.bot_models_startup import init_models_and_startup_tasks


class ModelStartupSecurityTest(unittest.TestCase):
    @patch("tools.ml_monitor.AlertManager")
    @patch("tools.ml_monitor.ModelPerformanceTracker")
    @patch("core.strategy.consensus_nn.AgentConsensusNN")
    def test_ml_monitor_uses_modular_consensus_model(
        self, consensus_cls, performance_cls, alerts_cls
    ):
        consensus_cls.return_value.is_trained = False
        bot = SimpleNamespace(
            ml_monitor=MagicMock(),
            ghost_model=None,
            log=MagicMock(),
        )

        init_ml_monitoring(bot, True)

        consensus_cls.assert_called_once_with()
        self.assertIs(bot.ml_performance, performance_cls.return_value)
        self.assertIs(bot.ml_alerts, alerts_cls.return_value)
        self.assertFalse(
            any("Error inicializando" in call.args[0] for call in bot.log.call_args_list)
        )

    def test_features_migration_reads_rowcount_from_cursor(self):
        cursor = SimpleNamespace(rowcount=2)
        connection = SimpleNamespace(
            execute=MagicMock(return_value=cursor),
            commit=MagicMock(),
            close=MagicMock(),
        )
        bot = SimpleNamespace(
            log=MagicMock(),
            _websocket_monitor=MagicMock(),
            ghost_model_type="OFF",
            bootstrap_heuristic_mode=False,
            ai_status_msg="",
            brain=SimpleNamespace(
                cleanup_stale_snapshots=MagicMock(return_value=0),
                _get_conn=MagicMock(return_value=connection),
            ),
            handle_command=MagicMock(),
        )

        with patch("core.bot_models_startup.threading.Thread") as thread_cls:
            thread_cls.return_value.start = MagicMock()
            init_models_and_startup_tasks(bot, None, None, None)

        self.assertTrue(
            any("Features version migrada: 2" in call.args[0] for call in bot.log.call_args_list)
        )

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

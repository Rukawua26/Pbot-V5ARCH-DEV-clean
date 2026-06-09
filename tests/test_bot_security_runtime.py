import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_connection import connect_to_binance
from core.bot_io_loops import telegram_listener


class BotSecurityRuntimeTest(unittest.TestCase):
    @patch("core.bot_io_loops.time.sleep", return_value=None)
    @patch("core.bot_io_loops.telegram_get_json")
    @patch("core.bot_io_loops.Config.TELEGRAM_CHAT_ID", "123")
    @patch("core.bot_io_loops.Config.TELEGRAM_TOKEN", "token")
    def test_telegram_listener_ignores_unauthorized_chat(self, mocked_updates, _mocked_sleep):
        bot = SimpleNamespace(is_running=True, handle_command=MagicMock(), log=MagicMock())

        def _updates(*_args, **_kwargs):
            bot.is_running = False
            return {
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "text": "/panic",
                            "chat": {"id": 999},
                        },
                    }
                ]
            }

        mocked_updates.side_effect = _updates
        telegram_listener(bot)

        bot.handle_command.assert_not_called()

    @patch("core.bot_io_loops.time.sleep", return_value=None)
    @patch("core.bot_io_loops.telegram_get_json")
    @patch("core.bot_io_loops.Config.TELEGRAM_CHAT_ID", "123")
    @patch("core.bot_io_loops.Config.TELEGRAM_TOKEN", "token")
    def test_telegram_listener_accepts_authorized_chat(self, mocked_updates, _mocked_sleep):
        bot = SimpleNamespace(is_running=True, handle_command=MagicMock(), log=MagicMock())

        def _handle(text):
            bot.is_running = False

        bot.handle_command.side_effect = _handle
        mocked_updates.return_value = {
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "text": "/status",
                        "chat": {"id": 123},
                    },
                }
            ]
        }

        telegram_listener(bot)

        bot.handle_command.assert_called_once_with("/status")

    @patch("core.bot_connection.Config.ALLOW_REAL_TRADING", True)
    @patch("core.bot_connection.Config.PAPER_MODE", False)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_aborts_when_balance_check_fails(self, mocked_binance):
        exchange = MagicMock()
        exchange.load_markets.return_value = None
        exchange.fetch_balance.side_effect = RuntimeError("invalid key")
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            execution=SimpleNamespace(exchange=None, load_markets=MagicMock()),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        with self.assertRaises(RuntimeError):
            connect_to_binance(bot)

        bot.sync_wallet.assert_not_called()

    @patch("core.bot_connection.Config.ALLOW_REAL_TRADING", True)
    @patch("core.bot_connection.Config.USE_TESTNET", False)
    @patch("core.bot_connection.Config.PAPER_MODE", False)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_resyncs_time_on_timestamp_drift(self, mocked_binance):
        exchange = MagicMock()
        exchange.load_time_difference.return_value = -1250
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            execution=SimpleNamespace(
                exchange=None,
                load_markets=MagicMock(),
                fetch_balance=MagicMock(
                    side_effect=[
                        RuntimeError(
                            'binance {"code":-1021,"msg":"Timestamp for this request was 1000ms ahead of the server\'s time."}'
                        ),
                        {"USDT": {"total": 1}},
                    ]
                ),
                fetch_position_mode=MagicMock(return_value={"hedged": False}),
                get_position_side_dual=MagicMock(return_value={"dualSidePosition": False}),
            ),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        connect_to_binance(bot)

        self.assertEqual(bot.execution.fetch_balance.call_count, 2)
        self.assertGreaterEqual(exchange.load_time_difference.call_count, 1)
        bot.sync_wallet.assert_called_once()

    @patch("core.bot_connection.Config.PAPER_MODE", True)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_keeps_booting_in_paper_when_balance_check_fails(
        self, mocked_binance
    ):
        exchange = MagicMock()
        exchange.load_markets.return_value = None
        exchange.fetch_balance.side_effect = RuntimeError("invalid key")
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            execution=SimpleNamespace(exchange=None, load_markets=MagicMock()),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        connect_to_binance(bot)

        bot.sync_wallet.assert_not_called()
        self.assertFalse(bot.is_hedge_mode)

    @patch("core.bot_connection.Config.ALLOW_REAL_TRADING", True)
    @patch("core.bot_connection.Config.USE_TESTNET", True)
    @patch("core.bot_connection.Config.PAPER_MODE", False)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_enables_sandbox_in_testnet(self, mocked_binance):
        exchange = MagicMock()
        exchange.fetch_balance.return_value = {"USDT": {"total": 1}}
        exchange.fetch_position_mode.return_value = {"hedged": False}
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            execution=SimpleNamespace(
                exchange=None,
                load_markets=MagicMock(),
                fetch_balance=MagicMock(return_value={"USDT": {"total": 1}}),
                fetch_position_mode=MagicMock(return_value={"hedged": False}),
                get_position_side_dual=MagicMock(return_value={"dualSidePosition": False}),
            ),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        connect_to_binance(bot)

        exchange.set_sandbox_mode.assert_called_once_with(True)

    @patch("core.bot_connection.Config.ALLOW_REAL_TRADING", True)
    @patch("core.bot_connection.Config.USE_TESTNET", False)
    @patch("core.bot_connection.Config.PAPER_MODE", False)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_aborts_when_position_mode_detection_fails(self, mocked_binance):
        exchange = MagicMock()
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            is_paused=False,
            integrity_lock_active=False,
            halt_system_active=False,
            execution=SimpleNamespace(
                exchange=None,
                load_markets=MagicMock(),
                fetch_balance=MagicMock(return_value={"USDT": {"total": 1}}),
                fetch_position_mode=MagicMock(side_effect=RuntimeError("mode down")),
                get_position_side_dual=MagicMock(side_effect=RuntimeError("mode down")),
            ),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            connect_to_binance(bot)

        self.assertIn("modo Hedge/OneWay", str(ctx.exception))
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        bot.sync_wallet.assert_not_called()

    @patch("core.bot_connection.Config.USE_TESTNET", True)
    @patch("core.bot_connection.Config.PAPER_MODE", True)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_falls_back_to_public_endpoints_in_paper_testnet(
        self, mocked_binance
    ):
        sandbox_exchange = MagicMock()
        sandbox_exchange.fetch_balance.return_value = {"USDT": {"total": 1}}
        public_exchange = MagicMock()
        public_exchange.fetch_balance.side_effect = RuntimeError("invalid key")
        bot_execution = SimpleNamespace(
            exchange=None,
            load_markets=MagicMock(
                side_effect=[
                    RuntimeError(
                        "binance does not have a testnet/sandbox URL for public endpoints"
                    ),
                    None,
                ]
            ),
            fetch_balance=MagicMock(side_effect=RuntimeError("invalid key")),
        )
        mocked_binance.side_effect = [sandbox_exchange, public_exchange]

        bot = SimpleNamespace(
            log=MagicMock(),
            execution=bot_execution,
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        connect_to_binance(bot)

        sandbox_exchange.set_sandbox_mode.assert_called_once_with(True)
        self.assertIs(bot.execution.exchange, public_exchange)
        self.assertEqual(bot_execution.load_markets.call_count, 2)
        bot.sync_wallet.assert_not_called()

    @patch("core.bot_connection.Config.ALLOW_REAL_TRADING", True)
    @patch("core.bot_connection.Config.USE_TESTNET", True)
    @patch("core.bot_connection.Config.PAPER_MODE", False)
    @patch("core.bot_connection.ccxt.binance")
    def test_connect_to_binance_fails_clearly_when_sandbox_activation_breaks(self, mocked_binance):
        exchange = MagicMock()
        exchange.set_sandbox_mode.side_effect = RuntimeError("unsupported")
        mocked_binance.return_value = exchange

        bot = SimpleNamespace(
            log=MagicMock(),
            execution=SimpleNamespace(exchange=None, load_markets=MagicMock()),
            data_service=SimpleNamespace(exchange=None),
            sync_wallet=MagicMock(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            connect_to_binance(bot)

        self.assertIn("testnet/sandbox", str(ctx.exception))
        bot.sync_wallet.assert_not_called()


if __name__ == "__main__":
    unittest.main()

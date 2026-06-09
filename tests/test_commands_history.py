import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.commands.history import _handle_history_commands


def _mock_bot(brain_methods=None):
    brain = SimpleNamespace(
        **{
            "get_paper_trades_history": MagicMock(return_value=[]),
            "get_stats_by_trend": MagicMock(return_value={}),
            "get_todays_trades": MagicMock(return_value=[]),
            "get_genetic_params": MagicMock(return_value=None),
            "get_trade_by_id": MagicMock(return_value=None),
            "get_similar_trades": MagicMock(return_value=[]),
            "get_agent_performance": MagicMock(return_value={}),
            "get_trades_by_market_regime": MagicMock(return_value={}),
            "get_recent_debug_logs": MagicMock(return_value=[]),
            "get_shadow_trades_history": MagicMock(return_value=[]),
            **(brain_methods or {}),
        }
    )
    return SimpleNamespace(brain=brain, scanner_history=[], log=MagicMock())


class TestHandleHistoryCommands(unittest.TestCase):
    @patch("core.commands.history.send_telegram_msg")
    def test_paper_review_empty(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/paper_review")
        self.assertTrue(result)
        mock_send.assert_called_once()

    @patch("core.commands.history.send_telegram_msg")
    def test_paper_review_with_trades(self, mock_send):
        trades = [
            {"pnl_percent": 2.0, "side": "BUY", "symbol": "BTC/USDT"},
            {"pnl_percent": -1.0, "side": "SELL", "symbol": "ETH/USDT"},
            {"pnl_percent": 3.0, "side": "BUY", "symbol": "SOL/USDT"},
        ]
        brain_methods = {"get_paper_trades_history": MagicMock(return_value=trades)}
        bot = _mock_bot(brain_methods)
        result = _handle_history_commands(bot, "/paper_review")
        self.assertTrue(result)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("Win Rate", msg)
        self.assertIn("66.7%", msg)  # 2 wins out of 3

    @patch("core.commands.history.send_telegram_msg")
    def test_performance_trends_empty(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/performance_trends")
        self.assertTrue(result)
        mock_send.assert_called_once()

    @patch("core.commands.history.send_telegram_msg")
    def test_performance_trends_with_data(self, mock_send):
        trends = {
            "UP": {"total": 3, "winrate": 66.7, "avg_pnl": 1.25},
            "RANGO": {"total": 2, "winrate": 50.0, "avg_pnl": -0.25},
        }
        brain_methods = {"get_stats_by_trend": MagicMock(return_value=trends)}
        bot = _mock_bot(brain_methods)
        result = _handle_history_commands(bot, "/performance_trends")
        self.assertTrue(result)
        mock_send.assert_called_once()

    @patch("core.commands.history.send_telegram_msg")
    def test_shadow_report_empty(self, mock_send):
        bot = _mock_bot({"get_todays_trades": MagicMock(return_value=[])})
        result = _handle_history_commands(bot, "/shadow_report")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("No se han registrado", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    @patch("core.commands.history.sqlite3.connect")
    def test_shadow_report_with_trades_and_db_row(self, mock_connect, mock_send):
        cursor = MagicMock()
        cursor.fetchone.return_value = (4, 3, 2.0, -1.0)
        mock_connect.return_value.cursor.return_value = cursor
        trades = [
            {"is_shadow": True, "pnl_percent": 1.0, "symbol": "BTC/USDT"},
            {"is_shadow": True, "pnl_percent": -0.5, "symbol": "BTC/USDT"},
            {"is_shadow": False, "pnl_percent": 3.0, "symbol": "ETH/USDT"},
        ]
        bot = _mock_bot({"get_todays_trades": MagicMock(return_value=trades)})
        result = _handle_history_commands(bot, "/shadow_report")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("BTC/USDT", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_dna_with_default_symbol_no_genes(self, mock_send):
        bot = _mock_bot({"get_genetic_params": MagicMock(return_value=None)})
        result = _handle_history_commands(bot, "/dna")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("Sin datos", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_dna_with_genes(self, mock_send):
        genes = {"sl_mult": 1.2, "tp_mult": 2.4, "generation": 7}
        bot = _mock_bot({"get_genetic_params": MagicMock(return_value=genes)})
        result = _handle_history_commands(bot, "/dna ETH/USDT")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("DNA STATUS", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_detail_usage_when_missing_symbol(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/trade_detail")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("Uso", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_detail_not_found(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/trade_detail BTC/USDT")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("No hay datos", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_detail_found_with_votes(self, mock_send):
        bot = _mock_bot()
        bot.scanner_history = [
            {
                "symbol": "BTC/USDT",
                "rsi_val": 55,
                "adx_val": 22,
                "z_score": 1.2,
                "ia_prob": "77%",
                "signal": "BUY",
                "result": "OK",
                "ob": "⚪",
                "trend_val": "UP",
                "funding_rate": 0.001,
                "votos": {"MT": 80, "SR": 60},
            }
        ]
        result = _handle_history_commands(bot, "/trade_detail BTC/USDT")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("VOTOS", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_usage_on_bad_id(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/trade bad")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("Uso", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_not_found(self, mock_send):
        bot = _mock_bot({"get_trade_by_id": MagicMock(return_value=None)})
        result = _handle_history_commands(bot, "/trade 123")
        self.assertTrue(result)
        mock_send.assert_called_once()
        self.assertIn("No se encontró", mock_send.call_args[0][0])

    @patch("core.commands.history.send_telegram_msg")
    def test_trade_found_with_snapshot_and_similar(self, mock_send):
        trade = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "entry_price": 100.0,
            "exit_price": 110.0,
            "pnl": 2.0,
            "pnl_percent": 10.0,
            "reason": "TP",
            "timestamp": "2026-01-01T00:00:00Z",
            "is_shadow": 1,
            "fees": 0.1,
            "rsi": 55,
            "adx": 20,
            "funding_rate": 0.001,
            "vol_rel": 1.3,
            "entry_ob": "⚪",
            "market_snapshot": '{"trend":"UP","z_score":1.2,"bb_pos":0.8,"dist_ema":0.02,"btc_delta_tf":0.5}',
        }
        similar = [{"id": 2, "symbol": "ETH/USDT", "pnl_percent": -1.5}]
        bot = _mock_bot(
            {
                "get_trade_by_id": MagicMock(return_value=trade),
                "get_similar_trades": MagicMock(return_value=similar),
            }
        )
        result = _handle_history_commands(bot, "/trade 1")
        self.assertTrue(result)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("ANÁLISIS IA", msg)
        self.assertIn("TRADES SIMILARES", msg)

    @patch("core.commands.history.send_telegram_msg")
    def test_unrecognized_command(self, mock_send):
        bot = _mock_bot()
        result = _handle_history_commands(bot, "/unknown_command")
        self.assertFalse(result)
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

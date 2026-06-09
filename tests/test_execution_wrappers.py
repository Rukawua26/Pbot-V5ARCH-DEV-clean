import unittest
from unittest.mock import patch


class TestHasMarketsLoaded(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_returns_false_when_no_markets(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.exchange.markets = None
        self.assertFalse(service.has_markets_loaded())

    @patch("core.execution_service.ccxt.binance")
    def test_returns_false_when_empty_markets(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.exchange.markets = {}
        self.assertFalse(service.has_markets_loaded())

    @patch("core.execution_service.ccxt.binance")
    def test_returns_true_when_markets_loaded(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.exchange.markets = {"BTC/USDT": {}}
        self.assertTrue(service.has_markets_loaded())


class TestLoadMarkets(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_calls_exchange_load_markets(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.load_markets()
        service.exchange.load_markets.assert_called_once()


class TestFetchBalance(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_calls_exchange_fetch_balance(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.fetch_balance()
        service.exchange.fetch_balance.assert_called_once()


class TestFetchPositions(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_calls_exchange_fetch_positions(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.fetch_positions()
        service.exchange.fetch_positions.assert_called_once()


class TestFetchOpenOrders(unittest.TestCase):
    @patch("core.execution_service.ccxt.binance")
    def test_calls_with_symbol(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.fetch_open_orders("BTC/USDT")
        service.exchange.fetch_open_orders.assert_called_once_with("BTC/USDT")

    @patch("core.execution_service.ccxt.binance")
    def test_calls_without_symbol(self, mock_binance):
        from core.execution_service import ExecutionService

        service = ExecutionService("key", "secret")
        service.fetch_open_orders()
        service.exchange.fetch_open_orders.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

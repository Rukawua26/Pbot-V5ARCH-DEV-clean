import os
import time
import unittest

from core.execution_service import ExecutionService


def _env_bool(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _position_contracts(exchange, symbol: str) -> float:
    positions = exchange.fetch_positions([symbol]) or []
    total = 0.0
    for position in positions:
        if str(position.get("symbol") or "").split(":")[0] != symbol.split(":")[0]:
            continue
        contracts = position.get("contracts")
        if contracts is None:
            contracts = (position.get("info") or {}).get("positionAmt", 0)
        total += float(contracts or 0.0)
    return total


def _cleanup_symbol(exchange, symbol: str) -> None:
    try:
        exchange.cancel_all_orders(symbol)
    except Exception:
        pass

    contracts = _position_contracts(exchange, symbol)
    if abs(contracts) <= 0.0:
        return

    side = "sell" if contracts > 0 else "buy"
    exchange.create_order(
        symbol,
        "market",
        side,
        abs(contracts),
        None,
        {"reduceOnly": True},
    )


@unittest.skipUnless(
    _env_bool("RUN_BINANCE_TESTNET_E2E"),
    "Set RUN_BINANCE_TESTNET_E2E=true to run live Binance Futures testnet E2E.",
)
class BinanceTestnetExecutionFlowTest(unittest.TestCase):
    """Opt-in integration test against Binance Futures Testnet.

    This is intentionally skipped in normal CI. It sends real testnet orders and
    must only run with dedicated testnet keys.
    """

    @classmethod
    def setUpClass(cls):
        api_key = os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
        if not api_key or not api_secret:
            raise unittest.SkipTest("Binance testnet API credentials are required")

        cls.symbol = os.getenv("BINANCE_TESTNET_SYMBOL", "BTC/USDT")
        cls.amount = float(os.getenv("BINANCE_TESTNET_ORDER_AMOUNT", "0.001"))
        if cls.amount <= 0:
            raise unittest.SkipTest("BINANCE_TESTNET_ORDER_AMOUNT must be positive")

        cls.execution = ExecutionService(api_key, api_secret)
        cls.execution.exchange.options["disableFuturesSandboxWarning"] = True
        cls.execution.exchange.set_sandbox_mode(True)
        cls.execution.exchange.load_time_difference()
        cls.execution.exchange.load_markets()
        if cls.symbol not in cls.execution.exchange.markets:
            fallback = f"{cls.symbol}:USDT" if ":" not in cls.symbol else cls.symbol
            if fallback not in cls.execution.exchange.markets:
                raise unittest.SkipTest(f"Symbol unavailable on testnet: {cls.symbol}")
            cls.symbol = fallback

    def tearDown(self):
        _cleanup_symbol(self.execution.exchange, self.symbol)

    def test_entry_hard_sl_close_and_flatten_flow(self):
        exchange = self.execution.exchange
        _cleanup_symbol(exchange, self.symbol)

        ticker = exchange.fetch_ticker(self.symbol) or {}
        last_price = float(ticker.get("last") or ticker.get("mark") or 0.0)
        self.assertGreater(last_price, 0.0)

        entry = self.execution.create_precision_order(
            self.symbol,
            "BUY",
            self.amount,
            last_price,
            slippage_pct=1.0,
            client_order_id=f"sai-e2e-entry-{int(time.time())}",
        )
        self.assertIsInstance(entry, dict)
        self.assertGreater(float((entry or {}).get("filled") or 0.0), 0.0)

        contracts = _position_contracts(exchange, self.symbol)
        self.assertGreater(abs(contracts), 0.0)

        sl_price = last_price * 0.95
        sl_order = self.execution.place_hard_sl(
            self.symbol,
            "BUY",
            abs(contracts),
            sl_price,
            client_order_id=f"sai-e2e-sl-{int(time.time())}",
        )
        self.assertIsInstance(sl_order, dict)
        self.assertTrue((sl_order or {}).get("id"))

        close_order = self.execution.close_position(self.symbol, "BUY", abs(contracts))
        self.assertIsInstance(close_order, dict)

        _cleanup_symbol(exchange, self.symbol)
        self.assertLessEqual(abs(_position_contracts(exchange, self.symbol)), 1e-12)


if __name__ == "__main__":
    unittest.main()

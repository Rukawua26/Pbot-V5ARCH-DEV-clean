from __future__ import annotations

import threading

import ccxt


class ClientOrderLookupExchange:
    def __init__(self, lookup_error=None, lookup_order=None):
        self.timeout = 9000
        self.lookup_error = lookup_error
        self.lookup_order = lookup_order
        self.create_attempts = 0
        self.lookup_attempts = 0

    def market_id(self, _symbol):
        return "BTCUSDT"

    def fapiPrivateGetOrder(self, _params):
        self.lookup_attempts += 1
        if self.lookup_error:
            raise self.lookup_error
        return self.lookup_order or {}

    def price_to_precision(self, _symbol, price):
        return str(price)

    def create_order(self, *args, **kwargs):
        self.create_attempts += 1
        raise ccxt.RequestTimeout("create ack timeout")


class NoPriceExchange:
    def __init__(self):
        self.timeout = 9000
        self.market_exit_calls = 0

    def cancel_all_orders(self, _symbol):
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 0}

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.market_exit_calls += 1
        return {
            "id": "mkt-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
            "status": "closed",
        }


class ChaseLimitNoFillExchange:
    def __init__(self):
        self.timeout = 9000
        self.created = []
        self.canceled = []

    def cancel_all_orders(self, _symbol):
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 100.0}

    def price_to_precision(self, _symbol, price):
        return str(round(float(price), 2))

    def create_order(self, symbol, order_type, side, amount, price, params):
        order = {
            "id": f"exit-{len(self.created) + 1}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "params": params,
        }
        self.created.append(order)
        return order

    def fetch_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "open"}

    def cancel_order(self, order_id, symbol):
        self.canceled.append(order_id)
        return {"id": order_id, "symbol": symbol, "status": "canceled"}


class TimeoutProbeExchange:
    def __init__(self):
        self.timeout = 9000

    def cancel_order(self, order_id, symbol):
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class AmbiguousIocExchange:
    def __init__(self):
        self.timeout = 9000
        self.fetch_attempts = 0

    def price_to_precision(self, _symbol, price):
        return str(price)

    def create_order(self, symbol, type, side, amount, price, params):
        return {
            "id": "entry-1",
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0.0,
            "params": params,
        }

    def fetch_order(self, order_id, symbol):
        self.fetch_attempts += 1
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "closed",
            "filled": 0.1,
            "average": 100.05,
        }


class ConcurrentTimeoutExchange:
    def __init__(self):
        self.timeout = 9000
        self._lock = threading.Lock()
        self.seen_timeouts = []

    def cancel_order(self, order_id, symbol):
        with self._lock:
            self.seen_timeouts.append(self.timeout)
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class ExchangeUnavailableOnce:
    def __init__(self):
        self.timeout = 9000
        self.fetch_attempts = 0

    def fetch_ticker(self, symbol):
        self.fetch_attempts += 1
        if self.fetch_attempts == 1:
            raise ccxt.ExchangeNotAvailable("502 Bad Gateway")
        return {"symbol": symbol, "last": 100.0}


class RateLimitedCloseExchange:
    def __init__(self):
        self.timeout = 9000
        self.cancel_attempts = 0
        self.created = []

    def cancel_all_orders(self, _symbol):
        self.cancel_attempts += 1
        if self.cancel_attempts == 1:
            raise ccxt.RateLimitExceeded("rate limited")
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 100.0}

    def price_to_precision(self, _symbol, price):
        return str(round(float(price), 2))

    def create_order(self, symbol, order_type, side, amount, price, params):
        order = {
            "id": f"close-{len(self.created) + 1}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "closed",
            "params": params,
        }
        self.created.append(order)
        return order

    def fetch_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "closed"}

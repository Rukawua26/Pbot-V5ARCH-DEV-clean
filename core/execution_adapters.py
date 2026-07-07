import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor


class ShadowExecutionAdapter:
    """Adapter de ejecución shadow-live con latencia/rechazo/fill parcial simulados."""

    def __init__(
        self,
        live_execution,
        *,
        min_latency_ms: int = 200,
        max_latency_ms: int = 500,
        reject_rate: float = 0.03,
        partial_fill_rate: float = 0.25,
        partial_fill_complete_rate: float = 0.5,
        price_out_of_range_rate: float = 0.05,
        min_partial_ratio: float = 0.3,
        simulated_balance_provider=None,
        random_source: random.Random | None = None,
        sleep_fn=time.sleep,
        executor: ThreadPoolExecutor | None = None,
    ):
        self._live = live_execution
        self.exchange = live_execution.exchange
        self.logger = getattr(live_execution, "logger", None)
        self.last_hard_sl_error = ""
        self.last_entry_reject_error = ""
        self._min_latency_ms = max(0, int(min_latency_ms))
        self._max_latency_ms = max(self._min_latency_ms, int(max_latency_ms))
        self._reject_rate = max(0.0, min(1.0, float(reject_rate)))
        self._partial_fill_rate = max(0.0, min(1.0, float(partial_fill_rate)))
        self._partial_fill_complete_rate = max(0.0, min(1.0, float(partial_fill_complete_rate)))
        self._price_out_of_range_rate = max(0.0, min(1.0, float(price_out_of_range_rate)))
        self._min_partial_ratio = max(0.05, min(0.95, float(min_partial_ratio)))
        self._rng = random_source or random.Random()
        self._simulated_balance_provider = simulated_balance_provider
        self._sleep = sleep_fn
        self._executor = executor or ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="shadow-exec"
        )
        self._own_executor = executor is None
        self._lock = threading.RLock()
        self._orders_by_id: dict[str, dict] = {}

    def set_exchange(self, exchange) -> None:
        self._live.exchange = exchange
        self.exchange = exchange

    def load_markets(self):
        return self._live.load_markets()

    def fetch_ticker(self, symbol: str) -> dict:
        return self._live.fetch_ticker(symbol)

    def fetch_my_trades(self, symbol: str, limit: int = 2):
        return self._live.fetch_my_trades(symbol, limit)

    def fetch_tickers(self, symbols=None, params=None):
        return self._live.fetch_tickers(symbols, params)

    def fetch_all_prices(self):
        return self._live.fetch_all_prices()

    def fetch_book_ticker(self, symbol: str):
        return self._live.fetch_book_ticker(symbol)

    def fetch_order_book(self, symbol: str, limit: int = 20):
        return self._live.fetch_order_book(symbol, limit)

    def fetch_positions(self):
        raise NotImplementedError(
            "Shadow adapter does not support fetch_positions — no real positions in shadow mode"
        )

    def get_balance(self) -> float:
        if callable(self._simulated_balance_provider):
            return float(self._simulated_balance_provider())
        raise NotImplementedError(
            "Shadow adapter does not support get_balance without a simulated balance provider"
        )

    def fetch_balance(self):
        balance = self.get_balance()
        return {"total": {"USDT": balance}, "free": {"USDT": balance}}

    def set_simulated_balance_provider(self, provider):
        self._simulated_balance_provider = provider

    def set_leverage(self, leverage: int, symbol: str):
        raise NotImplementedError(
            "Shadow adapter does not support set_leverage — no real leverage in shadow mode"
        )

    def set_weight_tracker(self, tracker):
        self._live.set_weight_tracker(tracker)

    def _sample_latency_ms(self) -> int:
        return self._rng.randint(self._min_latency_ms, self._max_latency_ms)

    def _reject(self) -> bool:
        return self._rng.random() < self._reject_rate

    def _mock_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        client_order_id: str | None,
        force_partial: bool = False,
        latency_ms: int = 0,
    ):
        ack_mono = time.monotonic()
        fill_after_mono = ack_mono + (max(0, int(latency_ms)) / 1000.0)
        partial = force_partial or (self._rng.random() < self._partial_fill_rate)
        if partial:
            ratio = self._rng.uniform(self._min_partial_ratio, 0.95)
            partial_qty = round(max(0.0, amount * ratio), 12)
            filled = partial_qty
            status = "open"
            fill_plan = (
                "partial_then_full"
                if self._rng.random() < self._partial_fill_complete_rate
                else "partial_stuck"
            )
            partial_after_mono = ack_mono
            partial_mono = ack_mono
        else:
            filled = round(max(0.0, amount), 12)
            status = "closed"
            fill_plan = "filled_on_ack"
            partial_qty = 0.0
            partial_after_mono = ack_mono
            partial_mono = None
        return {
            "id": f"shadow-{uuid.uuid4().hex[:16]}",
            "symbol": symbol,
            "side": side.lower(),
            "type": "limit",
            "status": status,
            "price": price,
            "average": price,
            "amount": amount,
            "filled": filled,
            "remaining": max(0.0, amount - filled),
            "clientOrderId": client_order_id,
            "info": {
                "shadow": True,
                "latency_profile": [self._min_latency_ms, self._max_latency_ms],
                "simulated_latency_ms": latency_ms,
                "shadow_ack_mono": ack_mono,
                "shadow_fill_after_mono": fill_after_mono,
                "shadow_fill_plan": fill_plan,
                "shadow_partial_qty": partial_qty,
                "shadow_partial_after_mono": partial_after_mono,
                "shadow_partial_mono": partial_mono,
                "shadow_full_mono": ack_mono if fill_plan == "filled_on_ack" else None,
            },
        }

    def _advance_order_state(self, order: dict, now_mono: float):
        if str(order.get("status") or "").lower() != "open":
            return

        remaining = float(order.get("remaining") or 0.0)
        if remaining <= 0.0:
            return

        info = order.get("info") or {}

        fill_plan = str(info.get("shadow_fill_plan") or "")
        if fill_plan == "filled_on_ack":
            return

        partial_mono = info.get("shadow_partial_mono")

        if fill_plan != "partial_then_full":
            return

        if partial_mono is None:
            return

        full_after = float(info.get("shadow_fill_after_mono") or 0.0)
        if now_mono < full_after:
            return

        current_filled = float(order.get("filled") or 0.0)
        current_avg = float(order.get("average") or order.get("price") or 0.0)
        base_price = float(order.get("price") or current_avg or 0.0)
        drift = self._rng.uniform(-0.0008, 0.0012)
        fill_price = base_price * (1.0 + drift)
        total_filled = current_filled + remaining
        if total_filled > 0:
            order["average"] = (
                (current_avg * current_filled) + (fill_price * remaining)
            ) / total_filled
        order["filled"] = total_filled
        order["remaining"] = 0.0
        order["status"] = "closed"
        info["shadow_full_mono"] = now_mono
        order["info"] = info

    def _advance_orders_locked(self):
        now_mono = time.monotonic()
        for order in self._orders_by_id.values():
            if isinstance(order, dict):
                self._advance_order_state(order, now_mono)

    def create_precision_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        slippage_pct: float = 0.1,
        client_order_id: str | None = None,
    ):
        self.last_entry_reject_error = ""
        if self._reject():
            self.last_entry_reject_error = "Random reject (shadow)"
            if self.logger:
                self.logger.warning(
                    f"⚠️ SHADOW EXEC reject {symbol} {side} clientId={client_order_id or 'N/A'}"
                )
            return None

        market_price = float((self._live.fetch_ticker(symbol) or {}).get("last") or price)
        shock = 0.0
        if self._rng.random() < self._price_out_of_range_rate:
            shock = self._rng.uniform(0.003, 0.01)
            if self._rng.random() < 0.5:
                shock = -shock
        effective_market = market_price * (1.0 + shock)
        allowed = abs(float(slippage_pct or 0.0))
        if effective_market > 0:
            diff_pct = abs((effective_market - float(price)) / effective_market) * 100.0
            if diff_pct > allowed:
                self.last_entry_reject_error = f"Price out of range: requested={price:.6f} market={effective_market:.6f} diff={diff_pct:.4f}% allowed={allowed:.4f}%"
                return None

        latency_ms = self._sample_latency_ms()
        order = self._mock_order(
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            client_order_id=client_order_id,
            latency_ms=latency_ms,
        )
        with self._lock:
            self._orders_by_id[order["id"]] = order

        return dict(order)

    def shutdown(self):
        if self._own_executor:
            self._executor.shutdown(wait=False)

    def fetch_open_orders(self, symbol: str | None = None):
        with self._lock:
            self._advance_orders_locked()
            orders = []
            for order in self._orders_by_id.values():
                if str(order.get("status") or "").lower() != "open":
                    continue
                if symbol and str(order.get("symbol") or "") != symbol:
                    continue
                orders.append(dict(order))
        return orders

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str):
        with self._lock:
            self._advance_orders_locked()
            for order in self._orders_by_id.values():
                if str(order.get("symbol") or "") != symbol:
                    continue
                coid = str(order.get("clientOrderId") or "")
                if coid and coid == str(client_order_id):
                    return dict(order)
        return None

    def place_hard_sl(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        client_order_id: str | None = None,
    ):
        ticker = self._live.fetch_ticker(symbol)
        market_price = float(ticker.get("last") or 0.0)
        is_buy_trade = str(side).lower() == "buy"
        invalid_trigger = (is_buy_trade and stop_price >= market_price) or (
            (not is_buy_trade) and stop_price <= market_price
        )
        if invalid_trigger:
            self.last_hard_sl_error = "Order would trigger immediately. (-2021)"
            return None
        self.last_hard_sl_error = ""
        return {
            "id": f"shadow-sl-{uuid.uuid4().hex[:14]}",
            "symbol": symbol,
            "type": "STOP_MARKET",
            "side": "sell" if is_buy_trade else "buy",
            "status": "open",
            "amount": amount,
            "stopPrice": stop_price,
            "clientOrderId": client_order_id,
            "info": {"shadow": True, "reduceOnly": True},
        }

    def close_position(self, symbol: str, side: str, amount: float):
        if self._reject():
            raise RuntimeError("shadow close rejected")
        price = float((self._live.fetch_ticker(symbol) or {}).get("last") or 0.0)
        return {
            "id": f"shadow-close-{uuid.uuid4().hex[:14]}",
            "symbol": symbol,
            "type": "market",
            "side": "sell" if str(side).lower() == "buy" else "buy",
            "status": "closed",
            "amount": amount,
            "filled": amount,
            "average": price,
            "info": {"shadow": True, "reduceOnly": True},
        }

    def create_reduce_only_market_order(self, symbol: str, side: str, amount: float, params=None):
        if self._reject():
            raise RuntimeError("shadow market close rejected")
        price = float((self._live.fetch_ticker(symbol) or {}).get("last") or 0.0)
        return {
            "id": f"shadow-market-close-{uuid.uuid4().hex[:14]}",
            "symbol": symbol,
            "type": "market",
            "side": str(side).lower(),
            "status": "closed",
            "amount": amount,
            "filled": amount,
            "average": price,
            "info": {"shadow": True, "reduceOnly": True},
        }

    def close_due_to_degradation(self, symbol: str, side: str, amount: float):
        return self.close_position(symbol, side, amount)

    def cancel_order(self, symbol: str, order_id: str):
        if self._reject():
            raise RuntimeError("shadow cancel rejected")
        with self._lock:
            order = self._orders_by_id.get(order_id)
            if isinstance(order, dict):
                order["status"] = "canceled"
                order["remaining"] = 0.0
                return dict(order)
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "info": {"shadow": True},
        }


def build_execution_gateway(config, execution_service_cls):
    execution = execution_service_cls(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

    if bool(getattr(config, "USE_TESTNET", False)):
        execution.exchange.options["disableFuturesSandboxWarning"] = True
        try:
            execution.exchange.set_sandbox_mode(True)
        except Exception as error:
            if getattr(execution, "logger", None):
                execution.logger.warning(f"⚠️ No se pudo activar sandbox mode: {error}")

    backend = str(getattr(config, "EXECUTION_BACKEND", "live") or "live").lower()
    if backend not in {"live", "shadow_live"}:
        raise RuntimeError(f"EXECUTION_BACKEND inválido: {backend}")
    if backend == "live" and bool(getattr(config, "PAPER_MODE", True)):
        allow_paper_live = bool(getattr(config, "ALLOW_PAPER_LIVE_GATEWAY", False))
        if not allow_paper_live:
            backend = "shadow_live"
    if backend == "shadow_live":
        if not bool(getattr(config, "PAPER_MODE", True)):
            raise RuntimeError("EXECUTION_BACKEND=shadow_live no está permitido en modo REAL")
        return ShadowExecutionAdapter(
            execution,
            min_latency_ms=int(getattr(config, "SHADOW_SIM_LATENCY_MIN_MS", 200)),
            max_latency_ms=int(getattr(config, "SHADOW_SIM_LATENCY_MAX_MS", 500)),
            reject_rate=float(getattr(config, "SHADOW_SIM_REJECT_RATE", 0.03)),
            partial_fill_rate=float(getattr(config, "SHADOW_SIM_PARTIAL_FILL_RATE", 0.25)),
            partial_fill_complete_rate=float(
                getattr(config, "SHADOW_SIM_PARTIAL_COMPLETE_RATE", 0.5)
            ),
            price_out_of_range_rate=float(
                getattr(config, "SHADOW_SIM_PRICE_OUT_OF_RANGE_RATE", 0.05)
            ),
            min_partial_ratio=float(getattr(config, "SHADOW_SIM_MIN_PARTIAL_RATIO", 0.3)),
        )
    return execution

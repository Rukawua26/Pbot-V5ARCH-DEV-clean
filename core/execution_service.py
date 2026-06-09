import logging
import math
import random
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import cast

import ccxt

from config import Config
from core.execution_order_helpers import (
    _execute_chase_limit_steps,
    _parse_order_float,
    _with_exit_state,
)
from core.types import CCXTBalanceResponse, CCXTOrder
from tools.notifier import send_telegram_msg


class OrderLookupError(RuntimeError):
    """Order lookup failed for operational reasons, not because order is absent."""


class ExecutionService:
    """
    [V118-ULTIMATE] EXECUTION SERVICE
    =================================
    Encapsula toda la comunicación con Binance Futures.
    Implementa el "Liquidity Guard" mediante órdenes LIMIT IOC.
    """

    def __init__(self, api_key, api_secret):
        self.exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "adjustForTimeDifference": True,
                "options": {"defaultType": "future"},
            }
        )
        self.logger = logging.getLogger("Execution")
        self.weight_tracker = None
        self.last_hard_sl_error = ""
        self.last_entry_reject_error = ""
        self._last_valid_balance: float | None = None
        self._exchange_call_lock = threading.RLock()
        self._account_lock = threading.RLock()
        self._cancel_all_failures = {}
        self._cancel_all_failure_events = {}
        self._symbol_quarantine_until = {}
        self._no_price_exit_state = {}
        self._no_price_exit_daily_metrics = {}

    def set_weight_tracker(self, tracker):
        self.weight_tracker = tracker

    def _track_api_weight(self, endpoint: str, weight: int, category: str):
        if self.weight_tracker:
            self.weight_tracker.track(endpoint, weight, category)

    def _call_exchange(
        self,
        op_name: str,
        fn,
        *,
        retries: int = 2,
        timeout_s: float = 0.0,
        _no_lock: bool = False,
    ):
        last_error = None
        lock_ctx = nullcontext() if _no_lock else self._exchange_call_lock
        for attempt in range(1, retries + 1):
            with lock_ctx:
                previous_timeout = getattr(self.exchange, "timeout", None)
                timeout_overridden = False
                try:
                    if timeout_s > 0:
                        self.exchange.timeout = int(timeout_s * 1000)
                        timeout_overridden = True
                    return fn()
                except ccxt.RateLimitExceeded as error:
                    last_error = error
                    if attempt >= retries:
                        break
                    sleep_s = (0.6 * attempt) + random.uniform(0.0, 0.3)
                    self.logger.warning(
                        f"⚠️ {op_name} rate-limit retry {attempt}/{retries}: {error}"
                    )
                except (ccxt.NetworkError, ccxt.RequestTimeout) as error:
                    last_error = error
                    if attempt >= retries:
                        break
                    sleep_s = (0.35 * attempt) + random.uniform(0.0, 0.2)
                    self.logger.warning(
                        f"⚠️ {op_name} network timeout/retry {attempt}/{retries}: {error}"
                    )
                except (ccxt.ExchangeError, Exception) as error:
                    last_error = error
                    break
                finally:
                    if timeout_overridden:
                        self.exchange.timeout = previous_timeout
            if attempt < retries:
                time.sleep(sleep_s)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{op_name} failed without captured error")

    def _call_exchange_account(self, op_name: str, fn, *, retries: int = 2, timeout_s: float = 0.0):
        with self._account_lock:
            return self._call_exchange(
                op_name,
                fn,
                retries=retries,
                timeout_s=timeout_s,
                _no_lock=True,
            )

    def _track_emergency_stuck(self, symbol: str, side: str, amount: float, order: dict):
        """Emite telemetría de emergencia cuando posición queda atrapada en libro."""
        self.logger.critical(
            f"🚨 EMERGENCY_EXIT_STUCK | {symbol} | {side} | "
            f"amount={amount} | order_id={order.get('id', 'N/A')}"
        )
        try:
            send_telegram_msg(
                f"🚨 *EMERGENCY_EXIT_STUCK*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 *{symbol}* ({side})\n"
                f"💰 Amount: {amount}\n"
                f"📋 Order ID: {order.get('id', 'N/A')}\n"
                f"⚠️ *INTERVENCIÓN MANUAL REQUERIDA*"
            )
        except (ccxt.NetworkError, ccxt.ExchangeError) as error:
            self.logger.warning(f"⚠️ No se pudo enviar alerta EMERGENCY_EXIT_STUCK: {error}")

    def _wait_order_filled(self, symbol: str, order_id: str, timeout_s: int) -> bool:
        start_wait = time.time()
        while time.time() - start_wait < timeout_s:
            try:
                status = self._call_exchange_account(
                    "fetch_order",
                    lambda: self.exchange.fetch_order(order_id, symbol),
                    retries=2,
                    timeout_s=15.0,
                )
                if status.get("status") in ["closed", "filled"]:
                    return True
            except (ccxt.NetworkError, ccxt.ExchangeError) as poll_error:
                self.logger.warning(
                    f"⚠️ Error consultando estado de orden {order_id} en {symbol}: {poll_error}"
                )
                return False
            time.sleep(0.5)
        return False

    def _confirm_ioc_order_state(
        self, symbol: str, order: CCXTOrder | dict | None, client_order_id: str | None
    ) -> CCXTOrder | dict | None:
        if not isinstance(order, dict):
            return order

        filled_amount = _parse_order_float(order, "filled", "executedQty") or 0.0
        status = str(order.get("status") or "").lower()
        if filled_amount > 0.0 or status in {"closed", "filled"}:
            return order

        order_id = order.get("id")
        timeout_s = float(getattr(Config, "ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS", 2.0) or 0.0)
        deadline = time.time() + max(0.0, timeout_s)
        last_seen = order

        while time.time() <= deadline:
            try:
                fetched = None
                if order_id:
                    fetched = self._call_exchange_account(
                        "confirm_ioc_fetch_order",
                        lambda: self.exchange.fetch_order(order_id, symbol),
                        retries=1,
                        timeout_s=10.0,
                    )
                    self._track_api_weight("fetch_order", 1, "account")
                elif client_order_id:
                    fetched = self.fetch_order_by_client_id(symbol, client_order_id)

                if isinstance(fetched, dict) and fetched:
                    last_seen = fetched
                    fetched_filled = _parse_order_float(fetched, "filled", "executedQty") or 0.0
                    fetched_status = str(fetched.get("status") or "").lower()
                    if fetched_filled > 0.0 or fetched_status in {"closed", "filled"}:
                        self.logger.info(
                            f"✅ IOC fill confirmado {symbol}: order_id={fetched.get('id', order_id)} "
                            f"filled={fetched_filled:g} status={fetched_status or 'N/A'}"
                        )
                        return fetched
                    if fetched_status in {"canceled", "cancelled", "expired", "rejected"}:
                        return fetched
            except (ccxt.NetworkError, ccxt.RequestTimeout, OrderLookupError) as error:
                self.logger.warning(
                    f"⚠️ IOC confirmación ambigua {symbol}/{order_id or client_order_id}: {error}"
                )
                break
            except ccxt.ExchangeError as error:
                self.logger.warning(
                    f"⚠️ IOC consulta exchange falló {symbol}/{order_id or client_order_id}: {error}"
                )
                break

            time.sleep(0.2)

        return last_seen

    def _record_cancel_all_orders_success(self, symbol: str):
        self._cancel_all_failures.pop(symbol, None)
        self._cancel_all_failure_events.pop(symbol, None)

    def _is_quarantine_active(self, symbol: str) -> bool:
        until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
        if until <= 0:
            return False
        if time.time() >= until:
            self._symbol_quarantine_until.pop(symbol, None)
            return False
        return True

    def is_symbol_quarantined(self, symbol: str) -> bool:
        return self._is_quarantine_active(symbol)

    def get_symbol_quarantine_remaining_seconds(self, symbol: str) -> int:
        until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
        if until <= 0:
            return 0
        remaining = int(max(0.0, until - time.time()))
        if remaining <= 0:
            self._symbol_quarantine_until.pop(symbol, None)
            return 0
        return remaining

    def _active_no_price_day_key(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def get_no_price_market_exit_count(self, symbol: str, day_key: str | None = None) -> int:
        key = day_key or self._active_no_price_day_key()
        return int((self._no_price_exit_daily_metrics.get(key) or {}).get(symbol, 0))

    def _resolve_no_price_threshold(self, symbol: str) -> int:
        base_threshold = int(getattr(Config, "NO_PRICE_EXIT_ESCALATION_SECONDS", 180) or 180)
        min_threshold = int(getattr(Config, "NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 45) or 45)
        daily_count = self.get_no_price_market_exit_count(symbol)
        dynamic_factor = 1.0 + (0.4 * min(daily_count, 5))
        dynamic_threshold = int(round(base_threshold / dynamic_factor))
        return max(min_threshold, dynamic_threshold)

    def _record_no_price_market_exit(self, symbol: str) -> int:
        day_key = self._active_no_price_day_key()
        day_metrics = self._no_price_exit_daily_metrics.setdefault(day_key, {})
        day_metrics[symbol] = int(day_metrics.get(symbol, 0)) + 1
        return day_metrics[symbol]

    def export_runtime_state(self) -> dict:
        now_ts = time.time()
        with self._exchange_call_lock:
            quarantines = {}
            for symbol, until in (self._symbol_quarantine_until or {}).items():
                try:
                    until_ts = float(until)
                except (TypeError, ValueError):
                    continue
                if until_ts > now_ts:
                    quarantines[str(symbol)] = until_ts

            day_key = self._active_no_price_day_key()
            daily_counts = dict(self._no_price_exit_daily_metrics.get(day_key) or {})

            return {
                "version": 1,
                "saved_at": now_ts,
                "quarantines": quarantines,
                "no_price_exit_daily": {day_key: daily_counts},
            }

    def import_runtime_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return

        now_ts = time.time()
        with self._exchange_call_lock:
            loaded_quarantines = {}
            for symbol, until in (state.get("quarantines") or {}).items():
                try:
                    until_ts = float(until)
                except (TypeError, ValueError):
                    continue
                if until_ts > now_ts:
                    loaded_quarantines[str(symbol)] = until_ts
            self._symbol_quarantine_until = loaded_quarantines

            persisted_daily = state.get("no_price_exit_daily") or {}
            day_key = self._active_no_price_day_key()
            day_metrics = persisted_daily.get(day_key) or {}
            if isinstance(day_metrics, dict):
                self._no_price_exit_daily_metrics = {
                    day_key: {
                        str(symbol): int(value)
                        for symbol, value in day_metrics.items()
                        if isinstance(value, (int, float)) and int(value) >= 0
                    }
                }
            else:
                self._no_price_exit_daily_metrics = {}

    def _record_cancel_all_orders_failure(self, symbol: str, error):
        now_ts = time.time()
        state = self._cancel_all_failures.get(symbol, {"count": 0})
        state["count"] = int(state.get("count") or 0) + 1
        state["last_error"] = str(error)
        state["last_ts"] = now_ts
        self._cancel_all_failures[symbol] = state

        events = self._cancel_all_failure_events.get(symbol, [])
        events.append(now_ts)
        window_s = int(getattr(Config, "CANCEL_ALL_DEGRADED_WINDOW_SECONDS", 300) or 300)
        cutoff = now_ts - window_s
        events = [evt for evt in events if evt >= cutoff]
        self._cancel_all_failure_events[symbol] = events

        count = state["count"]
        self.logger.warning(
            f"⚠️ cancel_all_orders fallo {symbol}: intento consecutivo {count}, error={error}"
        )

        quarantine_events = int(getattr(Config, "CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS", 3) or 3)
        if len(events) >= quarantine_events:
            quarantine_s = int(
                getattr(Config, "CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS", 900) or 900
            )
            quarantine_until = now_ts + quarantine_s
            previous_until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
            self._symbol_quarantine_until[symbol] = max(previous_until, quarantine_until)
            remaining_s = int(max(0.0, self._symbol_quarantine_until[symbol] - now_ts))
            self.logger.critical(
                f"🚫 SYMBOL_QUARANTINE_ACTIVATED {symbol}: {len(events)} fallos cancel_all en {window_s}s. "
                f"Quarantined {remaining_s}s."
            )

        if count >= 3:
            self.logger.critical(
                f"🚨 CANCEL_ALL_ORDERS_DEGRADED {symbol}: {count} fallos consecutivos"
            )
            try:
                send_telegram_msg(
                    f"🚨 *CANCEL_ALL_ORDERS_DEGRADED*\n"
                    f"Símbolo: {symbol}\n"
                    f"Fallos consecutivos: {count}\n"
                    f"Error: {str(error)[:180]}"
                )
            except Exception as notify_error:
                self.logger.warning(
                    f"⚠️ No se pudo notificar CANCEL_ALL_ORDERS_DEGRADED: {notify_error}"
                )

    def _handle_no_price_exit(self, symbol: str, exit_side: str, amount: float):
        now_mono = time.monotonic()
        state = self._no_price_exit_state.get(symbol) or {
            "first_seen": now_mono,
            "last_warn": 0.0,
        }

        threshold_s = self._resolve_no_price_threshold(symbol)
        allow_market = bool(getattr(Config, "NO_PRICE_ALLOW_MARKET_EXIT", False))
        elapsed_s = now_mono - float(state.get("first_seen") or now_mono)

        if elapsed_s < threshold_s or not allow_market:
            remaining = max(0.0, float(threshold_s) - elapsed_s)
            if now_mono - float(state.get("last_warn") or 0.0) > 30.0:
                self.logger.critical(
                    f"🛑 NO_PRICE {symbol}: salida bloqueada. "
                    f"Escalado en {remaining:.1f}s (allow_market={allow_market})."
                )
                state["last_warn"] = now_mono
            self._no_price_exit_state[symbol] = state
            return None

        try:
            order = self._call_exchange(
                "no_price_market_emergency_exit",
                lambda: self.exchange.create_order(
                    symbol, "market", exit_side, amount, None, {"reduceOnly": True}
                ),
                retries=2,
                timeout_s=20.0,
            )
            self._track_api_weight("create_order", 1, "trading")
            daily_count = self._record_no_price_market_exit(symbol)
            self.logger.critical(
                f"🚨 NO_PRICE_ESCALATED_MARKET_EXIT {symbol}: market reduce-only ejecutada "
                f"(daily_count={daily_count}, threshold_s={threshold_s})"
            )
            self._no_price_exit_state.pop(symbol, None)
            return order
        except Exception as error:
            self.logger.critical(f"❌ NO_PRICE_ESCALATED_MARKET_EXIT_FAILED {symbol}: {error}")
            self._no_price_exit_state[symbol] = state
            return None

    def has_markets_loaded(self) -> bool:
        try:
            return bool(getattr(self.exchange, "markets", None))
        except Exception as error:
            self.logger.warning(f"⚠️ No se pudo inspeccionar markets cargados: {error}")
            return False

    def load_markets(self):
        markets = self._call_exchange(
            "load_markets",
            lambda: self.exchange.load_markets(),
            retries=2,
            timeout_s=30.0,
        )
        self._track_api_weight("load_markets", 10, "essential")
        return markets

    def fetch_balance(self):
        balance = self._call_exchange_account(
            "fetch_balance",
            lambda: self.exchange.fetch_balance(),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fetch_balance", 5, "account")
        return balance

    def fetch_position_mode(self, symbol: str | None = None):
        if symbol:
            mode = self._call_exchange_account(
                "fetch_position_mode",
                lambda: self.exchange.fetch_position_mode(symbol=symbol),
                retries=2,
                timeout_s=10.0,
            )
        else:
            mode = self._call_exchange_account(
                "fetch_position_mode",
                lambda: self.exchange.fetch_position_mode(),
                retries=2,
                timeout_s=10.0,
            )
        self._track_api_weight("fetch_position_mode", 1, "account")
        return mode

    def get_position_side_dual(self):
        mode = self._call_exchange_account(
            "get_position_side_dual",
            lambda: self.exchange.fapiPrivateGetPositionSideDual(),
            retries=2,
            timeout_s=10.0,
        )
        self._track_api_weight("fapiPrivateGetPositionSideDual", 1, "account")
        return mode

    def fetch_tickers(self, symbols=None, params=None):
        if symbols is None:
            tickers = self._call_exchange(
                "fetch_tickers",
                lambda: self.exchange.fetch_tickers(params=params or {"type": "future"}),
                retries=2,
                timeout_s=20.0,
            )
        else:
            tickers = self._call_exchange(
                "fetch_tickers",
                lambda: self.exchange.fetch_tickers(symbols, params=params or {}),
                retries=2,
                timeout_s=20.0,
            )
        self._track_api_weight("fetch_tickers", 40, "market")
        return tickers

    def fetch_ticker(self, symbol: str):
        ticker = self._call_exchange(
            "fetch_ticker",
            lambda: self.exchange.fetch_ticker(symbol),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fetch_ticker", 1, "market")
        return ticker

    def fetch_positions(self):
        positions = self._call_exchange_account(
            "fetch_positions",
            lambda: self.exchange.fetch_positions(),
            retries=2,
            timeout_s=20.0,
        )
        self._track_api_weight("fetch_positions", 5, "account")
        return positions

    def fetch_open_orders(self, symbol: str | None = None):
        if symbol:
            orders = self._call_exchange_account(
                "fetch_open_orders",
                lambda: self.exchange.fetch_open_orders(symbol),
                retries=2,
                timeout_s=20.0,
            )
        else:
            orders = self._call_exchange_account(
                "fetch_open_orders",
                lambda: self.exchange.fetch_open_orders(),
                retries=2,
                timeout_s=20.0,
            )
        self._track_api_weight("fetch_open_orders", 5, "account")
        return orders

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str):
        if not symbol or not client_order_id:
            return None
        try:
            params = {
                "symbol": self.exchange.market_id(symbol),
                "origClientOrderId": client_order_id,
            }
            order = self._call_exchange_account(
                "fetch_order_by_client_id",
                lambda: self.exchange.fapiPrivateGetOrder(params),
                retries=2,
                timeout_s=15.0,
            )
            self._track_api_weight("fapiPrivateGetOrder", 1, "account")
            if isinstance(order, dict) and order:
                parsed = {
                    "id": order.get("orderId"),
                    "symbol": symbol,
                    "status": str(order.get("status", "")).lower(),
                    "clientOrderId": order.get("clientOrderId"),
                    "filled": _parse_order_float(order, "executedQty") or 0.0,
                    "remaining": _parse_order_float(order, "origQty") or 0.0,
                    "average": _parse_order_float(order, "avgPrice"),
                    "price": _parse_order_float(order, "price"),
                    "info": order,
                }
                if parsed.get("remaining"):
                    rem_raw = parsed.get("remaining", 0.0)
                    fil_raw = parsed.get("filled", 0.0)
                    if isinstance(rem_raw, dict) or isinstance(fil_raw, dict):
                        remaining = 0.0
                    else:
                        remaining = max(0.0, float(rem_raw or 0) - float(fil_raw or 0))
                    parsed["remaining"] = remaining
                return parsed
        except ccxt.OrderNotFound:
            return None
        except Exception as error:
            self.logger.warning(
                f"⚠️ No se pudo consultar orden por clientOrderId {symbol}/{client_order_id}: {error}"
            )
            raise OrderLookupError(str(error)) from error
        return None

    def fetch_my_trades(self, symbol: str, limit: int = 2):
        trades = self._call_exchange_account(
            "fetch_my_trades",
            lambda: self.exchange.fetch_my_trades(symbol, limit=limit),
            retries=2,
            timeout_s=20.0,
        )
        self._track_api_weight("fetch_my_trades", 5, "account")
        return trades

    def cancel_order(self, symbol: str, order_id: str):
        if not symbol or not order_id:
            return None
        canceled = self._call_exchange(
            "cancel_order",
            lambda: self.exchange.cancel_order(order_id, symbol),
            retries=3,
            timeout_s=20.0,
        )
        self._track_api_weight("cancel_order", 1, "trading")
        return canceled

    def fetch_all_prices(self):
        prices = self._call_exchange(
            "fetch_all_prices",
            lambda: self.exchange.fapiPublicGetTickerPrice(),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fapiPublicGetTickerPrice", 1, "market")
        return prices

    def fetch_book_tickers(self):
        books = self._call_exchange(
            "fetch_book_tickers",
            lambda: self.exchange.fapiPublicGetTickerBookTicker(),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fapiPublicGetTickerBookTicker", 1, "market")
        return books

    def fetch_book_ticker(self, symbol: str):
        market_id = self.exchange.market_id(symbol)
        book = self._call_exchange(
            "fetch_book_ticker",
            lambda: self.exchange.fapiPublicGetTickerBookTicker({"symbol": market_id}),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fapiPublicGetTickerBookTicker", 1, "market")
        if isinstance(book, list):
            return (book[0] if book else {}) or {}
        return book or {}

    def fetch_funding_rate(self, symbol: str):
        fr = self._call_exchange(
            "fetch_funding_rate",
            lambda: self.exchange.fetch_funding_rate(symbol),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fetch_funding_rate", 1, "market")
        return fr

    def fetch_open_interest(self, symbol: str) -> dict | None:
        """Fetch Open Interest para un símbolo de futuros. Peso API: 1."""
        try:
            oi = self._call_exchange_account(
                "fetch_open_interest",
                lambda: self.exchange.fetch_open_interest(symbol),
                retries=2,
                timeout_s=10.0,
            )
            self._track_api_weight("fetch_open_interest", 1, "market")
            return oi
        except Exception as e:
            self.logger.warning(f"⚠️ OI fetch falló para {symbol}: {e}")
            return None

    def fetch_order_book(self, symbol: str, limit: int = 20):
        ob = self._call_exchange(
            "fetch_order_book",
            lambda: self.exchange.fetch_order_book(symbol, limit=limit),
            retries=2,
            timeout_s=15.0,
        )
        self._track_api_weight("fetch_order_book", 1, "market")
        return ob

    def create_reduce_only_market_order(self, symbol: str, side: str, amount: float, params=None):
        order = self._call_exchange(
            "create_reduce_only_market_order",
            lambda: self.exchange.create_order(
                symbol,
                "MARKET",
                side.lower(),
                amount,
                None,
                params=(params or {"reduceOnly": True}),
            ),
            retries=3,
            timeout_s=25.0,
        )
        self._track_api_weight("create_order", 1, "trading")
        return order

    def set_leverage(self, leverage, symbol):
        try:
            requested_leverage = leverage
            try:
                bounded_leverage = int(float(leverage))
            except (TypeError, ValueError):
                bounded_leverage = int(getattr(Config, "LEVERAGE", 10))
            bounded_leverage = max(1, min(bounded_leverage, 10))
            if str(bounded_leverage) != str(requested_leverage):
                self.logger.warning(
                    f"⚠️ Leverage ajustado por guardrail: {requested_leverage}x -> {bounded_leverage}x ({symbol})"
                )

            result = self._call_exchange(
                "set_leverage",
                lambda: self.exchange.set_leverage(bounded_leverage, symbol),
                retries=2,
                timeout_s=15.0,
            )
            self._track_api_weight("set_leverage", 1, "trading")
            return result
        except Exception as e:
            self.logger.error(f"Error setting leverage for {symbol}: {e}")
            return None

    def create_precision_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        slippage_pct: float = 0.1,
        client_order_id: str | None = None,
    ) -> CCXTOrder | None:
        """
        Ejecución quirúrgica: LIMIT IOC.
        Si no se llena al precio límite (con slippage), se cancela automáticamente.
        """
        if self._is_quarantine_active(symbol):
            remaining_s = self.get_symbol_quarantine_remaining_seconds(symbol)
            self.last_entry_reject_error = (
                f"SYMBOL_QUARANTINED_CANCEL_ALL_DEGRADED ({remaining_s}s)"
            )
            self.logger.warning(f"🚫 ENTRY_BLOCKED_QUARANTINE {symbol}: {remaining_s}s restantes")
            return None

        try:
            # Calcular precio límite basado en slippage permitido
            limit_price = (
                price * (1 + (slippage_pct / 100))
                if side.lower() == "buy"
                else price * (1 - (slippage_pct / 100))
            )

            # Formatear precio para Binance
            limit_price_str = self.exchange.price_to_precision(symbol, limit_price)

            self.logger.info(
                f"🎯 Precio Base: {price} | Slippage: {slippage_pct}% | Límite IOC: {limit_price_str}"
            )

            params = {
                "timeInForce": "IOC",  # Immediate or Cancel
                "postOnly": False,
            }
            if client_order_id:
                params["newClientOrderId"] = client_order_id

            self.logger.info(f"🚀 Enviando LIMIT IOC {symbol} {side} @ {limit_price_str}")

            order: CCXTOrder = self._call_exchange(
                "create_precision_order",
                lambda: self.exchange.create_order(
                    symbol,
                    type="limit",
                    side=side.lower(),
                    amount=amount,
                    price=float(limit_price_str),
                    params=params,
                ),
                retries=1 if client_order_id else 3,
                timeout_s=25.0,
            )
            self._track_api_weight("create_order", 1, "trading")

            return cast(
                CCXTOrder | None, self._confirm_ioc_order_state(symbol, order, client_order_id)
            )
        except Exception as e:
            if client_order_id:
                try:
                    recovered = self.fetch_order_by_client_id(symbol, client_order_id)
                    if recovered:
                        self.logger.warning(
                            f"⚠️ Entrada {symbol} recuperada por clientOrderId tras error: {client_order_id}"
                        )
                        return recovered
                except Exception as lookup_error:
                    self.logger.warning(
                        f"⚠️ No se pudo recuperar entrada ambigua {symbol}/{client_order_id}: {lookup_error}"
                    )
            self.logger.error(f"❌ Error en Ejecución Quirúrgica {symbol}: {e}")
            return None

    def get_balance(self) -> float:
        last_error = None

        for attempt in range(2):
            try:
                balance: CCXTBalanceResponse = self._call_exchange_account(
                    "get_balance",
                    lambda: self.exchange.fetch_balance(),
                    retries=1,
                    timeout_s=15.0,
                )
                self._track_api_weight("fetch_balance", 5, "account")

                info = balance.get("info", {})
                total_wallet = info.get("totalWalletBalance")
                if total_wallet is not None:
                    parsed = float(total_wallet)
                    self._last_valid_balance = parsed
                    return parsed

                total = balance.get("total", {})
                parsed = float(total.get("USDT", 0.0))
                self._last_valid_balance = parsed
                return parsed
            except Exception as e:
                last_error = e
                msg = str(e)
                timestamp_error = (
                    "-1021" in msg
                    or "recvWindow" in msg
                    or "Timestamp for this request is outside of the recvWindow" in msg
                )

                if timestamp_error and attempt == 0:
                    self.logger.warning(
                        "⚠️ Error de timestamp detectado al leer balance. Re-sincronizando reloj con Binance y reintentando..."
                    )
                    try:
                        if hasattr(self.exchange, "load_time_difference"):
                            self._call_exchange(
                                "load_time_difference",
                                lambda: self.exchange.load_time_difference(),
                                retries=1,
                                timeout_s=10.0,
                                _no_lock=False,
                            )
                        elif hasattr(self.exchange, "fetch_time"):
                            self._call_exchange(
                                "fetch_time",
                                lambda: self.exchange.fetch_time(),
                                retries=1,
                                timeout_s=10.0,
                                _no_lock=False,
                            )
                    except Exception as sync_error:
                        self.logger.warning(
                            f"⚠️ No se pudo sincronizar diferencia horaria: {sync_error}"
                        )
                    time.sleep(0.35)
                    continue

                break

        self.logger.error(f"Error fetching balance: {last_error}")
        if self._last_valid_balance is not None:
            self.logger.warning(
                f"⚠️ Usando último balance válido en caché: ${self._last_valid_balance:.2f}"
            )
            return float(self._last_valid_balance)
        return 0.0

    def place_hard_sl(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        client_order_id: str | None = None,
    ) -> CCXTOrder | None:
        """Coloca un STOP_MARKET real en Binance para seguridad extrema."""
        try:
            stop_price_float = float(stop_price)
        except (TypeError, ValueError):
            stop_price_float = float("nan")
        if not math.isfinite(stop_price_float) or stop_price_float <= 0.0:
            self.last_hard_sl_error = f"Invalid stop_price: {stop_price} (must be finite and > 0)"
            self.logger.error(self.last_hard_sl_error)
            return None
        try:
            sl_side = "sell" if side.lower() == "buy" else "buy"
            params = {
                "stopPrice": self.exchange.price_to_precision(symbol, stop_price_float),
                "reduceOnly": True,
            }
            if client_order_id:
                params["newClientOrderId"] = client_order_id
            order = self._call_exchange(
                "place_hard_sl",
                lambda: self.exchange.create_order(
                    symbol, "STOP_MARKET", sl_side, amount, None, params
                ),
                retries=3,
                timeout_s=25.0,
            )
            self._track_api_weight("create_order", 1, "trading")
            self.last_hard_sl_error = ""
            return order
        except Exception as e:
            self.last_hard_sl_error = str(e)
            self.logger.error(f"⚠️ Error colocando Hard SL {symbol}: {e}")
            return None

    def _close_position_chase(
        self,
        symbol: str,
        side: str,
        amount: float,
        context_label: str = "",
        emergency_op_name: str = "close_position_emergency_create_order",
        hard_floor_label: str = "",
        emergency_fail_label: str = "",
    ) -> CCXTOrder | None:
        exit_side = "sell" if side.lower() == "buy" else "buy"
        params = {"reduceOnly": True}

        try:
            self._call_exchange(
                "cancel_all_orders",
                lambda: self.exchange.cancel_all_orders(symbol),
                retries=3,
                timeout_s=20.0,
            )
            self._track_api_weight("cancel_all_orders", 1, "trading")
            self._record_cancel_all_orders_success(symbol)
        except Exception as error:
            self._record_cancel_all_orders_failure(symbol, error)

        current_price = float(
            (
                self._call_exchange_account(
                    "fetch_ticker",
                    lambda: self.exchange.fetch_ticker(symbol),
                    retries=2,
                    timeout_s=15.0,
                )
                or {}
            ).get("last", 0)
            or 0
        )

        if current_price > 0:
            last_order = _execute_chase_limit_steps(
                self, symbol, exit_side, amount, current_price, params
            )

            if last_order and last_order.get("exit_state") == "FILLED":
                return last_order

            prefix = f" ({hard_floor_label})" if hard_floor_label else ""
            if last_order:
                self.logger.critical(
                    f"🚨 HARD_FLOOR_REACHED{prefix} {symbol}: posición atrapada en libro @ "
                    f"{last_order.get('price', 'N/A')}. Alerta manual requerida."
                )
                self._track_emergency_stuck(symbol, exit_side, amount, cast(dict, last_order))
                return _with_exit_state(last_order, "STUCK")
            else:
                self.logger.warning(
                    f"⚠️ Sin fill tras persecución {symbol}, ordenando al precio actual"
                )
                try:
                    emergency_price = self.exchange.price_to_precision(symbol, current_price)
                    order = self._call_exchange(
                        emergency_op_name,
                        lambda: self.exchange.create_order(
                            symbol,
                            "limit",
                            exit_side,
                            amount,
                            emergency_price,
                            params,
                        ),
                        retries=3,
                        timeout_s=20.0,
                    )
                    self._track_api_weight("create_order", 1, "trading")
                    self._no_price_exit_state.pop(symbol, None)
                    return _with_exit_state(cast(dict, order), "OPEN_UNCONFIRMED")
                except Exception as emergency_err:
                    fatal_label = f" ({emergency_fail_label})" if emergency_fail_label else ""
                    self.logger.critical(
                        f"❌ EMERGENCY_EXIT_FAILED{fatal_label} {symbol}: {emergency_err}"
                    )
                    return None
        else:
            return self._handle_no_price_exit(symbol, exit_side, amount)

    def close_position(self, symbol: str, side: str, amount: float) -> CCXTOrder | None:
        """
        [v119-CHASE-LIMIT] Cierra posición con Chase Limit + Hard Floor.
        - -2% inicial, persigue hasta -5% (Hard Floor)
        - Si Hard Floor no llena: deja orden en libro + EMERGENCY_EXIT_STUCK
        - NUNCA fallback a MARKET
        """
        try:
            return self._close_position_chase(
                symbol,
                side,
                amount,
                emergency_op_name="close_position_emergency_create_order",
            )
        except Exception as e:
            self.logger.error(f"❌ Error cerrando posición {symbol}: {e}")
            raise e

    def close_due_to_degradation(self, symbol: str, side: str, amount: float) -> CCXTOrder | None:
        """
        [v119-CHASE-LIMIT] Cierra por degradación neuronal con Chase Limit + Hard Floor.
        - -2% inicial, persigue hasta -5% (Hard Floor)
        - Si Hard Floor no llena: deja orden en libro + EMERGENCY_EXIT_STUCK
        - NUNCA fallback a MARKET
        """
        self.logger.warning(
            f"⚠️ [SMART EXIT] Chase Limit (-2%→-5%) por degradación neuronal en {symbol} ({side})"
        )
        try:
            return self._close_position_chase(
                symbol,
                side,
                amount,
                context_label="degradation",
                emergency_op_name="close_degradation_emergency_create_order",
                hard_floor_label="degradation",
                emergency_fail_label="degradation",
            )
        except Exception as e:
            self.logger.critical(
                f"❌ FATAL ERROR ejecutando Salida por Degradación en {symbol}: {e}"
            )
            return None

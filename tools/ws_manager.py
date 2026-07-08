import asyncio
from collections import deque
import json
import logging
import random
import threading
import time

import websockets

logger = logging.getLogger("SniperAI")


class BinanceWebSocket:
    def __init__(
        self,
        symbols=["btcusdt"],
        enable_cvd=False,
        cvd_window_seconds=300,
        on_reconnect=None,
    ):
        self.symbols = [s.lower().replace("/", "").split(":")[0] for s in symbols]
        self.enable_cvd = bool(enable_cvd)
        self.cvd_window_seconds = max(1, int(cvd_window_seconds or 300))
        self.on_reconnect = on_reconnect
        self._lock = threading.RLock()

        self.url = self._build_url()

        self.l2_state = {
            s: {"bid": 0.0, "ask": 0.0, "spread": 0.0} for s in self.symbols
        }
        self.cvd_state = {
            s: {"events": deque(), "buy_volume": 0.0, "sell_volume": 0.0, "cvd": 0.0}
            for s in self.symbols
        }
        self.is_running = False
        self._loop = None
        self._ws = None
        self._thread = None
        self._connect_count = 0
        self._reconnect_count = 0

    def _build_url(self):
        streams = []
        for s in self.symbols:
            streams.append(f"{s}@depth5@100ms")
            if self.enable_cvd:
                streams.append(f"{s}@aggTrade")
        if len(streams) == 1:
            return f"wss://fstream.binance.com/ws/{streams[0]}"
        return f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"

    def update_symbols(self, symbols):
        """Dynamically updates the watched symbols and reconnects."""
        new_symbols = [s.lower().replace("/", "").split(":")[0] for s in symbols]
        if set(self.symbols) != set(new_symbols):
            self.symbols = new_symbols
            self.url = self._build_url()

            # Keep existing state for intersecting symbols, initialize new ones
            new_state = {}
            new_cvd_state = {}
            for s in self.symbols:
                new_state[s] = self.l2_state.get(
                    s, {"bid": 0.0, "ask": 0.0, "spread": 0.0}
                )
                new_cvd_state[s] = self.cvd_state.get(
                    s,
                    {"events": deque(), "buy_volume": 0.0, "sell_volume": 0.0, "cvd": 0.0},
                )
            self.l2_state = new_state
            self.cvd_state = new_cvd_state

            if self.is_running:
                # Signal reconnect
                self._reconnect_flag = True

    async def start(self):
        """Starts the WebSocket connection and maintains an infinite listening loop."""
        self._reconnect_flag = False
        reconnect_delay = 2.0
        while self.is_running:
            try:
                # Use \n to avoid mixing with the carriage return updates
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    self._connect_count += 1
                    if self._connect_count > 1:
                        self._reconnect_count += 1
                        callback = self.on_reconnect
                        if callable(callback):
                            threading.Thread(
                                target=callback,
                                kwargs={
                                    "source": "l2_cvd_ws",
                                    "reconnect_count": self._reconnect_count,
                                },
                                daemon=True,
                            ).start()
                    self._reconnect_flag = False
                    reconnect_delay = 2.0
                    try:
                        while self.is_running and not self._reconnect_flag:
                            try:
                                message = await asyncio.wait_for(ws.recv(), timeout=10)
                                data = json.loads(message)
                                self._process_data(data)
                            except (websockets.ConnectionClosed, asyncio.TimeoutError):
                                break
                    finally:
                        self._ws = None
            except Exception as e:
                logger.warning("WS reconnect loop error: %s", e)

            if self.is_running:
                wait_s = reconnect_delay + random.uniform(0.0, 0.8)
                await asyncio.sleep(wait_s)
                reconnect_delay = min(reconnect_delay * 1.7, 30.0)

    def _process_data(self, data):
        """Extracts best bid/ask, calculates spread, and updates state."""
        try:
            # Handle combined stream format {"stream": "btcusdt@depth5...", "data": {...}}
            if "stream" in data and "data" in data:
                stream_name = data["stream"]
                symbol = stream_name.split("@")[0]
                payload = data["data"]
                stream_type = stream_name.split("@", 1)[1] if "@" in stream_name else ""
            else:
                # Single stream format
                symbol = self.symbols[0]
                payload = data
                stream_type = str(payload.get("e", ""))

            if self.enable_cvd and ("aggTrade" in stream_type or payload.get("e") == "aggTrade"):
                self._process_agg_trade(symbol, payload)
                return

            # 'b' for bids, 'a' for asks
            bids = payload.get("b", [])
            asks = payload.get("a", [])

            if not bids or not asks:
                return

            # Best Bid is the highest price (first in the array usually)
            best_bid = float(bids[0][0])
            # Best Ask is the lowest price
            best_ask = float(asks[0][0])

            spread_abs = best_ask - best_bid
            spread_pct = (spread_abs / best_ask) * 100 if best_ask > 0 else 0.0

            with self._lock:
                if symbol in self.l2_state:
                    self.l2_state[symbol]["bid"] = best_bid
                    self.l2_state[symbol]["ask"] = best_ask
                    self.l2_state[symbol]["spread"] = spread_pct

        except (ValueError, IndexError, KeyError) as e:
            logger.warning("WS payload inválido: %s", e)

    def _process_agg_trade(self, symbol, payload):
        try:
            price = float(payload.get("p", 0.0) or 0.0)
            qty = float(payload.get("q", 0.0) or 0.0)
            if price <= 0 or qty <= 0:
                return
            ts = float(payload.get("T") or payload.get("E") or time.time() * 1000.0) / 1000.0
            quote_volume = price * qty
            # Binance aggTrade: m=True means buyer is maker, so seller was aggressor.
            is_sell_aggressor = bool(payload.get("m", False))
            delta = -quote_volume if is_sell_aggressor else quote_volume
            with self._lock:
                state = self.cvd_state.setdefault(
                    symbol,
                    {"events": deque(), "buy_volume": 0.0, "sell_volume": 0.0, "cvd": 0.0},
                )
                state["events"].append((ts, delta))
                if delta >= 0:
                    state["buy_volume"] += delta
                else:
                    state["sell_volume"] += abs(delta)
                state["cvd"] += delta
                self._prune_cvd_locked(symbol, now_ts=ts)
        except (TypeError, ValueError) as e:
            logger.warning("WS aggTrade inválido: %s", e)

    def _prune_cvd_locked(self, symbol, now_ts=None):
        state = self.cvd_state.get(symbol)
        if not state:
            return
        now = float(now_ts if now_ts is not None else time.time())
        cutoff = now - self.cvd_window_seconds
        events = state.get("events")
        while events and events[0][0] < cutoff:
            _ts, delta = events.popleft()
            state["cvd"] -= delta
            if delta >= 0:
                state["buy_volume"] -= delta
            else:
                state["sell_volume"] -= abs(delta)
        state["buy_volume"] = max(0.0, float(state.get("buy_volume", 0.0)))
        state["sell_volume"] = max(0.0, float(state.get("sell_volume", 0.0)))

    def start_background(self):
        """Starts the WebSocket loop in a background daemon thread."""
        self.is_running = True
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_running = False
        self._reconnect_flag = True
        ws = self._ws
        loop = self._loop
        if ws is not None and loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(ws.close(), loop)

    def _run_async_loop(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())
        finally:
            self._loop = None
            loop.close()

    def get_l2_spread(self, symbol):
        """Returns the spread in percentage for a specific symbol."""
        sym = symbol.lower().replace("/", "").split(":")[0]
        with self._lock:
            if sym in self.l2_state:
                return self.l2_state[sym]["spread"]
        return None

    def get_l2_state(self, symbol=None):
        """Returns full L2 state for a symbol or all symbols."""
        if symbol:
            sym = symbol.lower().replace("/", "").split(":")[0]
            with self._lock:
                state = self.l2_state.get(sym)
                return dict(state) if state else None
        with self._lock:
            return {sym: dict(state) for sym, state in self.l2_state.items()}

    def get_cvd_state(self, symbol):
        """Returns rolling CVD state for a symbol based on aggTrade aggressors."""
        sym = symbol.lower().replace("/", "").split(":")[0]
        with self._lock:
            self._prune_cvd_locked(sym)
            state = self.cvd_state.get(sym)
            if not state:
                return None
            buy = float(state.get("buy_volume", 0.0) or 0.0)
            sell = float(state.get("sell_volume", 0.0) or 0.0)
            total = buy + sell
            imbalance = (buy - sell) / total if total > 0 else 0.0
            return {
                "cvd": float(state.get("cvd", 0.0) or 0.0),
                "buy_volume": buy,
                "sell_volume": sell,
                "total_volume": total,
                "imbalance": imbalance,
                "events": len(state.get("events", [])),
                "window_seconds": self.cvd_window_seconds,
            }


if __name__ == "__main__":
    ws = BinanceWebSocket(symbols=["btcusdt", "ethusdt"])
    ws.start_background()
    print("🚀 WebSocket iniciado en segundo plano. Leyendo estado...")
    for i in range(5):
        time.sleep(1)
        print(
            f"Tick {i + 1}: BTC: {ws.get_l2_spread('BTC/USDT')}% | ETH: {ws.get_l2_spread('ETH/USDT')}%"
        )
    ws.stop()
    print("🏁 Test finalizado.")

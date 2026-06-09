import threading
import time


class IntentDeduper:
    _window: int
    _lock: threading.RLock
    _registry: dict[str, float]

    def __init__(self, window_seconds: int = 300):
        self._window = window_seconds
        self._lock = threading.RLock()
        self._registry = {}

    def _make_key(self, symbol: str, side: str, signal_ts: float) -> str:
        window_key = int(signal_ts / self._window) * self._window
        return f"{symbol.upper()}:{side.upper()}:{window_key}"

    def check_and_register(self, symbol: str, side: str, signal_ts: float) -> bool:
        key = self._make_key(symbol, side, signal_ts)
        with self._lock:
            if key in self._registry:
                return False
            self._registry[key] = time.time()
            self._evict()
        return True

    def is_duplicate(self, symbol: str, side: str, signal_ts: float) -> bool:
        key = self._make_key(symbol, side, signal_ts)
        with self._lock:
            return key in self._registry

    def _evict(self):
        cutoff = time.time() - self._window * 2
        stale = [k for k, v in self._registry.items() if v < cutoff]
        for k in stale:
            del self._registry[k]

    def clear(self):
        with self._lock:
            self._registry.clear()

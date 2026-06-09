import threading
from typing import Any


class CandleCloseCache:
    _lock: threading.RLock
    _cache: dict[tuple[str, str, str], tuple[int, Any]]
    _max: int

    def __init__(self, max_entries: int = 500):
        self._lock = threading.RLock()
        self._cache: dict[tuple[str, str, str], tuple[int, Any]] = {}
        self._max = max_entries

    def get(self, namespace: str, symbol: str, timeframe: str, candle_close_ms: int) -> Any | None:
        key = (namespace, symbol.upper(), timeframe)
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[0] == candle_close_ms:
                return entry[1]
        return None

    def set(self, namespace: str, symbol: str, timeframe: str, candle_close_ms: int, value: Any):
        key = (namespace, symbol.upper(), timeframe)
        with self._lock:
            self._cache[key] = (candle_close_ms, value)
            if len(self._cache) > self._max:
                self._evict()

    def is_new_candle(
        self, namespace: str, symbol: str, timeframe: str, candle_close_ms: int
    ) -> bool:
        key = (namespace, symbol.upper(), timeframe)
        with self._lock:
            entry = self._cache.get(key)
            return entry is None or entry[0] != candle_close_ms

    def _evict(self):
        sorted_items = sorted(self._cache.items(), key=lambda x: x[1][0])
        remove_count = len(self._cache) - self._max // 2
        for k, _ in sorted_items[:remove_count]:
            del self._cache[k]

    def clear(self):
        with self._lock:
            self._cache.clear()

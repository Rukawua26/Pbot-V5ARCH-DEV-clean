import hashlib
import json
import os
import threading
import time

import pandas as pd

from config import Config

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FvgStore:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def save(self, gaps: list[dict]) -> None:
        with self._lock:
            tmp = self._path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(gaps, f, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def load(self) -> list[dict]:
        with self._lock:
            if not os.path.exists(self._path):
                return []
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []


def _compute_gap_id(symbol: str, gap_type: str, formed_at: int) -> str:
    raw = f"{symbol}|{gap_type}|{formed_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _make_gap(
    symbol: str,
    gap_type: str,
    gap_high: float,
    gap_low: float,
    gap_pct: float,
    formed_at: int,
    expires_bars: int,
) -> dict:
    expires_ms = formed_at + expires_bars * 3600 * 1000
    return {
        "id": _compute_gap_id(symbol, gap_type, formed_at),
        "symbol": symbol,
        "type": gap_type,
        "gap_high": round(gap_high, 8),
        "gap_low": round(gap_low, 8),
        "gap_pct": round(gap_pct, 4),
        "formed_at": formed_at,
        "status": "ACTIVE",
        "filled_at": None,
        "expires_at": expires_ms,
        "last_alert_pct": None,
    }


def _detect_fvg(df: pd.DataFrame, symbol: str, min_gap_pct: float, expires_bars: int) -> list[dict]:
    if df is None or len(df) < 3:
        return []
    gaps = []
    df_sorted = df.sort_values("time")
    for i in range(2, len(df_sorted)):
        c0 = df_sorted.iloc[i - 2]
        c2 = df_sorted.iloc[i]
        c0_low = float(c0["low"])
        c0_high = float(c0["high"])
        c2_low = float(c2["low"])
        c2_high = float(c2["high"])
        formed_at = int(c2["time"])

        if c2_low > c0_high:
            gap_high = c2_low
            gap_low = c0_high
            gap_pct = (gap_high - gap_low) / gap_low * 100
            if gap_pct >= min_gap_pct:
                gaps.append(
                    _make_gap(
                        symbol,
                        "BULLISH_FVG",
                        gap_high,
                        gap_low,
                        gap_pct,
                        formed_at,
                        expires_bars,
                    )
                )
        if c2_high < c0_low:
            gap_high = c0_low
            gap_low = c2_high
            gap_pct = (gap_high - gap_low) / gap_low * 100
            if gap_pct >= min_gap_pct:
                gaps.append(
                    _make_gap(
                        symbol,
                        "BEARISH_FVG",
                        gap_high,
                        gap_low,
                        gap_pct,
                        formed_at,
                        expires_bars,
                    )
                )
    return gaps


def _update_gap_states(
    gaps: list[dict], df: pd.DataFrame | None, now_ms: int, symbol: str | None = None
) -> list[dict]:
    if df is not None and not df.empty:
        recent = df.sort_values("time").tail(10)
        low_min: float | None = float(recent["low"].min())
        high_max: float | None = float(recent["high"].max())
    else:
        low_min = None
        high_max = None
    updated = []
    for g in gaps:
        if g["status"] not in {"ACTIVE", "PARTIAL_FILL"}:
            updated.append(g)
            continue
        if g["expires_at"] and now_ms >= g["expires_at"]:
            g["status"] = "INVALIDATED"
            updated.append(g)
            continue
        if symbol is not None and g.get("symbol") != symbol:
            updated.append(g)
            continue
        if low_min is not None and high_max is not None and df is not None:
            gap_low_min = low_min
            gap_high_max = high_max
            if "time" in df.columns and g.get("formed_at") is not None:
                post_formation = df[df["time"] > int(g["formed_at"])]
                if post_formation.empty:
                    updated.append(g)
                    continue
                recent = post_formation.sort_values("time").tail(10)
                gap_low_min = float(recent["low"].min())
                gap_high_max = float(recent["high"].max())
            g_high = g["gap_high"]
            g_low = g["gap_low"]
            if gap_high_max >= g_high and gap_low_min <= g_low:
                g["status"] = "FILLED"
                g["filled_at"] = now_ms
            elif gap_high_max >= g_low and gap_low_min <= g_high:
                g["status"] = "PARTIAL_FILL"
        updated.append(g)
    return updated


class FvgTracker:
    def __init__(
        self,
        enabled: bool = False,
        min_gap_pct: float = 0.1,
        max_candles_scan: int = 200,
        alert_throttle_seconds: int = 3600,
        expiration_bars: int = 48,
        telegram_alerts: bool = True,
        max_symbols_per_cycle: int = 20,
        store_path: str | None = None,
    ):
        self.enabled = enabled
        self.min_gap_pct = min_gap_pct
        self.max_candles_scan = max_candles_scan
        self.alert_throttle_seconds = alert_throttle_seconds
        self.expiration_bars = expiration_bars
        self.telegram_alerts = telegram_alerts
        self.max_symbols_per_cycle = max(1, int(max_symbols_per_cycle))
        self._store = FvgStore(
            store_path or os.path.join(_BASE_DIR, "data_storage", "fvg_gaps.json")
        )
        self._active_gaps: list[dict] = []
        self._lock = threading.Lock()
        self._last_alert: dict[str, float] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._active_gaps = self._store.load()
            self._loaded = True

    def get_active_gaps(self, symbol: str | None = None) -> list[dict]:
        with self._lock:
            self._ensure_loaded()
            if symbol is None:
                return list(self._active_gaps)
            return [g for g in self._active_gaps if g["symbol"] == symbol]

    def _merge_new_gaps(self, new_gaps: list[dict]) -> list[dict]:
        existing_ids = {g["id"] for g in self._active_gaps}
        merged = list(self._active_gaps)
        for ng in new_gaps:
            if ng["id"] not in existing_ids:
                merged.append(ng)
                existing_ids.add(ng["id"])
        return merged

    def scan_candles(self, df: pd.DataFrame, symbol: str) -> list[dict]:
        return _detect_fvg(df, symbol, self.min_gap_pct, self.expiration_bars)

    def run_cycle(self, bot) -> None:
        self._ensure_loaded()
        symbols = sorted(bot.live_prices.keys())[: self.max_symbols_per_cycle]
        if not symbols:
            return

        now_ms = int(time.time() * 1000)
        all_new = []
        dfs_by_symbol = {}

        for raw_symbol in symbols:
            try:
                df = bot.data_service.fetch_and_update_data(raw_symbol, "1h")
                if df is None or len(df) < 3:
                    continue
                df = df.tail(self.max_candles_scan)
                dfs_by_symbol[raw_symbol] = df
                new_gaps = self.scan_candles(df, raw_symbol)
                all_new.extend(new_gaps)
            except Exception as error:
                if hasattr(bot, "log"):
                    bot.log(f"FVG Tracker fetch error {raw_symbol}: {error}")

        with self._lock:
            self._active_gaps = self._merge_new_gaps(all_new)
            self._active_gaps = _update_gap_states(self._active_gaps, None, now_ms)
            for raw_symbol, df in dfs_by_symbol.items():
                self._active_gaps = _update_gap_states(
                    self._active_gaps, df, now_ms, symbol=raw_symbol
                )

        if self.telegram_alerts:
            self._evaluate_and_alert(bot)

        self._store.save(self._active_gaps)

    def _evaluate_and_alert(self, bot) -> None:
        from tools.notifier import Priority, send_telegram_msg

        now = time.time()
        with bot.price_lock:
            price_map = {k: float(v) for k, v in bot.live_prices.items() if v}

        thresholds = [0.5, 1.0]
        for gap in self._active_gaps:
            if gap["status"] != "ACTIVE":
                continue
            price = price_map.get(gap["symbol"])
            if price is None or price <= 0:
                continue
            gap_low = float(gap["gap_low"])
            gap_high = float(gap["gap_high"])
            if gap_low <= price <= gap_high:
                distance_pct = 0.0
            else:
                nearest_edge = gap_low if price < gap_low else gap_high
                distance_pct = abs(price - nearest_edge) / nearest_edge * 100
            if any(distance_pct <= t for t in thresholds):
                last = self._last_alert.get(gap["id"], 0)
                if now - last >= self.alert_throttle_seconds:
                    msg = (
                        f"FVG Alert [{gap['symbol']}]\n"
                        f"Type: {gap['type']}\n"
                        f"Zone: {gap['gap_low']:.8f} - {gap['gap_high']:.8f}\n"
                        f"Distance: {distance_pct:.2f}%\n"
                        f"Price: {price:.8f}"
                    )
                    send_telegram_msg(msg, Priority.INFO)
                    self._last_alert[gap["id"]] = now


def run_fvg_tracker_loop(bot) -> None:
    tracker = bot.fvg_tracker
    interval = getattr(Config, "FVG_SCAN_INTERVAL", 300)
    while bot.is_running:
        try:
            tracker.run_cycle(bot)
        except Exception as e:
            if hasattr(bot, "log"):
                bot.log(f"FVG Tracker error: {e}")
        time.sleep(interval)

from __future__ import annotations

import time
from typing import Any

import pandas as pd


class BreakoutAgent:
    """
    Agente de acecho de ruptura institucional.

    - No consulta API externa.
    - Trabaja con DataFrames ya disponibles en memoria.
    - Modo pasivo: detecta y emite señal de ruptura potencial.
    """

    def __init__(
        self,
        min_ia_prob: float = 60.0,
        volume_multiplier: float = 1.5,
        breakout_buffer_pct: float = 0.5,
        timeout_minutes: int = 60,
    ) -> None:
        self.min_ia_prob = float(min_ia_prob)
        self.volume_multiplier = float(volume_multiplier)
        self.breakout_buffer_pct = float(breakout_buffer_pct)
        self.timeout_minutes = int(timeout_minutes)
        self.watchlist: dict[str, dict[str, Any]] = {}

    def add_to_watchlist(
        self,
        symbol: str,
        side: str,
        ia_prob: float,
        shock_level: float,
        trend: str,
        metadata: dict[str, Any] | None = None,
        min_ia_prob: float | None = None,
    ) -> bool:
        if not symbol or side not in {"BUY", "SELL"}:
            return False
        min_prob = float(min_ia_prob) if min_ia_prob is not None else float(self.min_ia_prob)
        if ia_prob < min_prob or shock_level is None:
            return False

        now = time.time()
        row = self.watchlist.get(symbol)
        payload = {
            "symbol": symbol,
            "side": side,
            "ia_prob": float(ia_prob),
            "shock_level": float(shock_level),
            "trend": str(trend or "RANGO"),
            "created_at": row.get("created_at", now) if row else now,
            "updated_at": now,
            "touches": int(row.get("touches", 0) + 1) if row else 1,
            "meta": metadata or {},
        }
        self.watchlist[symbol] = payload
        return True

    def evaluate_breakout(
        self, symbol: str, df: pd.DataFrame | None
    ) -> tuple[bool, dict[str, Any] | None]:
        if symbol not in self.watchlist:
            return False, None
        if df is None or df.empty:
            return False, None
        if not all(col in df.columns for col in ["close", "volume"]):
            return False, None
        if len(df) < 25:
            return False, None

        w = self.watchlist[symbol]
        side = str(w.get("side", "BUY"))
        shock_level = float(w.get("shock_level", 0.0) or 0.0)
        if shock_level <= 0:
            return False, None

        last_close = float(df["close"].iloc[-1])
        current_volume = float(df["volume"].iloc[-1])
        avg_volume = float(df["volume"].tail(20).mean())
        if avg_volume <= 0:
            return False, None

        vol_ok = current_volume >= (avg_volume * self.volume_multiplier)
        buffer = self.breakout_buffer_pct / 100.0

        if side == "BUY":
            price_ok = last_close > shock_level * (1.0 + buffer)
        else:
            price_ok = last_close < shock_level * (1.0 - buffer)

        if not (price_ok and vol_ok):
            return False, None

        payload = {
            "symbol": symbol,
            "side": side,
            "ia_prob": float(w.get("ia_prob", 0.0)),
            "shock_level": shock_level,
            "breakout_close": last_close,
            "breakout_buffer_pct": self.breakout_buffer_pct,
            "volume_now": current_volume,
            "volume_avg20": avg_volume,
            "volume_multiplier_req": self.volume_multiplier,
            "detected_at": time.time(),
        }
        return True, payload

    def clean_stale_watchlist(self) -> int:
        if not self.watchlist:
            return 0
        now = time.time()
        ttl = max(1, self.timeout_minutes) * 60
        stale = [
            symbol
            for symbol, row in self.watchlist.items()
            if (now - float(row.get("updated_at", row.get("created_at", now)))) > ttl
        ]
        for symbol in stale:
            self.watchlist.pop(symbol, None)
        return len(stale)

    def size(self) -> int:
        return len(self.watchlist)

    def summary_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.watchlist.values():
            meta = row.get("meta") or {}
            source = str(meta.get("source", "UNKNOWN"))
            out[source] = int(out.get(source, 0) + 1)
        return out

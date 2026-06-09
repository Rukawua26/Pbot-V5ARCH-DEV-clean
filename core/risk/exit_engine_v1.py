from __future__ import annotations

from datetime import datetime
from typing import Any

from config import Config
from core.time_utils import parse_datetime_utc, utc_now


class ExitEngineV1:
    """Motor de salida dinámico v1.2: BE guard, trailing multi-etapa y time decay por volatilidad."""

    def __init__(
        self,
        time_decay_bars: int = 4,
        escape_velocity_pct: float = 0.2,
        structural_atr_buffer: float = 0.25,
        structural_min_buffer_pct: float = 0.05,
        structural_min_hold_seconds: int = 120,
        trailing_activation_pct: float = 0.9,
        trailing_atr_mult: float = 2.0,
        trailing_atr_mult_tight: float = 1.5,
        trailing_tighten_pnl_pct: float = 2.0,
        trailing_min_distance_pct: float = 0.3,
        breakeven_trigger_pct: float = 0.8,
        breakeven_atr_mult: float = 1.2,
        breakeven_lock_pct: float = 0.1,
        flat_time_decay_bars: int = 3,
        flat_time_decay_atr_mult: float = 0.5,
    ) -> None:
        self.time_decay_bars = int(time_decay_bars)
        self.escape_velocity_pct = float(escape_velocity_pct)
        self.structural_atr_buffer = float(structural_atr_buffer)
        self.structural_min_buffer_pct = float(structural_min_buffer_pct)
        self.structural_min_hold_seconds = int(structural_min_hold_seconds)
        self.trailing_activation_pct = float(trailing_activation_pct)
        self.trailing_atr_mult = float(trailing_atr_mult)
        self.trailing_atr_mult_tight = float(trailing_atr_mult_tight)
        self.trailing_tighten_pnl_pct = float(trailing_tighten_pnl_pct)
        self.trailing_min_distance_pct = float(trailing_min_distance_pct)
        self.breakeven_trigger_pct = float(breakeven_trigger_pct)
        self.breakeven_atr_mult = float(breakeven_atr_mult)
        self.breakeven_lock_pct = float(breakeven_lock_pct)
        self.flat_time_decay_bars = int(flat_time_decay_bars)
        self.flat_time_decay_atr_mult = float(flat_time_decay_atr_mult)

    def _bars_elapsed(self, open_time: Any, timeframe_minutes: int = 60) -> int:
        try:
            open_time = parse_datetime_utc(open_time)
        except Exception:
            return 0
        mins = max(0.0, (utc_now() - open_time).total_seconds() / 60.0)
        return int(mins // max(1, timeframe_minutes))

    def check_time_decay_exit(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        pnl_pct = float(trade.get("pnl", 0.0))
        bars = self._bars_elapsed(trade.get("open_time"), timeframe_minutes=60)

        # Corrección cuantitativa: velocidad de escape, no "zona muerta"
        if bars >= self.time_decay_bars and pnl_pct < self.escape_velocity_pct:
            return {
                "should_exit": True,
                "reason": "TIME_DECAY_ESCAPE_VELOCITY",
                "meta": {
                    "bars_elapsed": bars,
                    "pnl_pct": pnl_pct,
                    "escape_velocity_pct": self.escape_velocity_pct,
                },
            }
        return None

    def check_structural_invalidation_exit(
        self, trade: dict[str, Any], current_price: float, current_atr: float
    ) -> dict[str, Any] | None:
        # Solo aplica para operaciones originadas en breakout.
        if not bool(trade.get("breakout_origin", False)):
            return None

        # Evitar cierres instantáneos por ruido/spread justo tras abrir.
        open_time = trade.get("open_time")
        try:
            open_time = parse_datetime_utc(open_time)
        except Exception:
            open_time = None
        if isinstance(open_time, datetime):
            elapsed = (utc_now() - open_time).total_seconds()
            if elapsed < self.structural_min_hold_seconds:
                return None

        side = str(trade.get("side", "BUY"))
        shock_level = trade.get("entry_shock_level")
        entry = float(trade.get("entry", 0.0) or 0.0)
        if shock_level is None or entry <= 0:
            return None

        shock_level = float(shock_level)
        atr_buffer_pct = 0.0
        if current_atr > 0:
            atr_buffer_pct = (current_atr / entry) * 100.0 * self.structural_atr_buffer
        buffer_pct = max(self.structural_min_buffer_pct, atr_buffer_pct)

        if side == "BUY":
            invalidation = shock_level * (1.0 - buffer_pct / 100.0)
            broken = current_price < invalidation
        else:
            invalidation = shock_level * (1.0 + buffer_pct / 100.0)
            broken = current_price > invalidation

        if broken:
            return {
                "should_exit": True,
                "reason": "STRUCTURAL_INVALIDATION",
                "meta": {
                    "current_price": current_price,
                    "shock_level": shock_level,
                    "invalidation_level": invalidation,
                    "buffer_pct": buffer_pct,
                },
            }
        return None

    def check_atr_trailing_exit(
        self, trade: dict[str, Any], current_atr: float
    ) -> dict[str, Any] | None:
        pnl = float(trade.get("pnl", 0.0))
        peak = float(trade.get("peak_pnl", pnl))
        entry = float(trade.get("entry", 0.0) or 0.0)
        lev = float(trade.get("leverage", 1.0) or 1.0)

        if entry <= 0 or pnl < self.trailing_activation_pct:
            return None

        atr_mult = (
            self.trailing_atr_mult_tight
            if pnl >= self.trailing_tighten_pnl_pct
            else self.trailing_atr_mult
        )

        atr_distance_pct = 0.0
        if current_atr > 0:
            atr_distance_pct = (current_atr / entry) * 100.0 * lev * atr_mult
        trail_distance = max(self.trailing_min_distance_pct, atr_distance_pct)

        if pnl <= (peak - trail_distance):
            return {
                "should_exit": True,
                "reason": "ATR_TRAILING_HIT",
                "meta": {
                    "pnl_pct": pnl,
                    "peak_pnl_pct": peak,
                    "trail_distance_pct": trail_distance,
                    "atr_mult": atr_mult,
                    "tightened": pnl >= self.trailing_tighten_pnl_pct,
                },
            }
        return None

    def check_breakeven_guard(
        self, trade: dict[str, Any], current_atr: float
    ) -> dict[str, Any] | None:
        entry = float(trade.get("entry", 0.0) or 0.0)
        pnl = float(trade.get("pnl", 0.0) or 0.0)
        side = str(trade.get("side", "BUY"))
        lev = float(trade.get("leverage", 1.0) or 1.0)

        if entry <= 0:
            return None

        trigger_pct = float(self.breakeven_trigger_pct)
        if current_atr > 0:
            atr_trigger_pct = (current_atr / entry) * 100.0 * lev * self.breakeven_atr_mult
            trigger_pct = max(trigger_pct, atr_trigger_pct)

        if pnl < trigger_pct:
            return None

        lock_mult = 1.0 + (self.breakeven_lock_pct / 100.0)
        be_sl = entry * lock_mult if side == "BUY" else entry * (2.0 - lock_mult)

        current_sl = trade.get("sl")
        if current_sl is None:
            current_sl = 0.0 if side == "BUY" else float("inf")
        current_sl = float(current_sl)

        if side == "BUY":
            should_tighten = be_sl > current_sl
        else:
            should_tighten = be_sl < current_sl

        if should_tighten:
            trade["sl"] = be_sl

        if not bool(trade.get("exit_be_armed", False)):
            trade["exit_be_armed"] = True
            return {
                "should_exit": False,
                "reason": "BREAKEVEN_GUARD_ARMED",
                "meta": {
                    "pnl_pct": pnl,
                    "trigger_pct": trigger_pct,
                    "be_sl": be_sl,
                    "tightened": should_tighten,
                },
            }
        return None

    def check_flat_volatility_exit(
        self, trade: dict[str, Any], current_price: float, current_atr: float
    ) -> dict[str, Any] | None:
        entry = float(trade.get("entry", 0.0) or 0.0)
        if entry <= 0 or current_atr <= 0:
            return None

        bars = self._bars_elapsed(trade.get("open_time"), timeframe_minutes=60)
        if bars < self.flat_time_decay_bars:
            return None

        moved = abs(float(current_price) - entry)
        required = float(current_atr) * self.flat_time_decay_atr_mult
        if moved < required:
            moved_pct = (moved / entry) * 100.0
            required_pct = (required / entry) * 100.0
            return {
                "should_exit": True,
                "reason": "TIME_DECAY_FLAT_VOLATILITY",
                "meta": {
                    "bars_elapsed": bars,
                    "moved_abs": moved,
                    "required_abs": required,
                    "moved_pct": moved_pct,
                    "required_pct": required_pct,
                },
            }
        return None

    def evaluate_exit(
        self,
        trade: dict[str, Any],
        current_price: float,
        current_atr: float,
        threshold_factor: float | None = None,
    ) -> dict[str, Any]:
        if threshold_factor is None:
            threshold_factor = (
                Config.SMART_EXIT_THRESHOLD_SHADOW
                if trade.get("is_shadow", False)
                else Config.SMART_EXIT_THRESHOLD_REAL
            )
        # Prioridad: invalidación estructural -> trailing ATR -> flat volatility -> time decay
        result = self.check_structural_invalidation_exit(trade, current_price, current_atr)
        if result:
            return result

        # Side-effect controlado: mueve SL a BE cuando hay colchón suficiente.
        be_result = self.check_breakeven_guard(trade, current_atr)

        result = self.check_atr_trailing_exit(trade, current_atr)
        if result:
            return result

        result = self.check_flat_volatility_exit(trade, current_price, current_atr)
        if result:
            return result

        result = self.check_time_decay_exit(trade)
        if result:
            return result

        if be_result:
            return be_result

        return {"should_exit": False, "reason": "HOLD", "meta": {}}

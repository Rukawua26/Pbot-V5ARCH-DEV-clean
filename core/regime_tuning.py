from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import Config

logger = logging.getLogger("RegimeTuning")

_STATS_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_STATS_PATH = _STATS_DIR / "regime_tuning_stats.json"


def _ensure_dir():
    _STATS_DIR.mkdir(parents=True, exist_ok=True)


def _load_stats() -> dict[str, Any]:
    _ensure_dir()
    if _STATS_PATH.exists():
        try:
            return json.loads(_STATS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar regime_tuning_stats: {e}")
    return {}


def _save_stats(stats: dict[str, Any]):
    _ensure_dir()
    try:
        _STATS_PATH.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar regime_tuning_stats: {e}")


def record_trade(regime: str, pnl_percent: float):
    if not regime:
        return
    regime = str(regime).upper()
    stats = _load_stats()
    entry = stats.setdefault(regime, {"trades": 0, "wins": 0, "sum_pnl": 0.0})
    entry["trades"] += 1
    if pnl_percent > 0:
        entry["wins"] += 1
    entry["sum_pnl"] = round(entry["sum_pnl"] + pnl_percent, 4)
    _save_stats(stats)


def get_sl_multiplier(bot, regime: str) -> float:
    return _get_regime_mult(bot, regime, "sl")


def get_tp_multiplier(bot, regime: str) -> float:
    return _get_regime_mult(bot, regime, "tp")


def _get_regime_mult(bot, regime: str, kind: str) -> float:
    if not bool(getattr(Config, "REGIME_TUNING_ENABLED", False)):
        return 1.0

    regime = str(regime).upper() if regime else ""
    if regime not in {"BULL_TREND", "BEAR_TREND", "RANGE"}:
        return 1.0

    stats = _load_stats()
    entry = stats.get(regime, {})
    n = entry.get("trades", 0)
    min_trades = int(getattr(Config, "REGIME_TUNING_MIN_TRADES", 5))
    if n < min_trades:
        return 1.0

    wins = entry.get("wins", 0)
    wr = wins / n if n > 0 else 0.5

    sl_max = float(getattr(Config, "REGIME_TUNING_SL_RANGE_MAX", 1.20))
    sl_min = float(getattr(Config, "REGIME_TUNING_SL_RANGE_MIN", 0.60))
    tp_max = float(getattr(Config, "REGIME_TUNING_TP_RANGE_MAX", 1.30))
    tp_min = float(getattr(Config, "REGIME_TUNING_TP_RANGE_MIN", 0.70))

    if kind == "sl":
        if wr < 0.35:
            return sl_min
        elif wr < 0.45:
            return 0.80
        elif wr > 0.65:
            return sl_max
        elif wr > 0.55:
            return 1.10
        return 1.0
    else:
        if wr < 0.35:
            return tp_min
        elif wr < 0.45:
            return 0.85
        elif wr > 0.65:
            return tp_max
        elif wr > 0.55:
            return 1.15
        return 1.0


def get_stats_summary() -> str:
    stats = _load_stats()
    if not stats:
        return "RegimeTuning: sin datos"
    parts = []
    for regime in sorted(stats.keys()):
        d = stats[regime]
        n = d.get("trades", 0)
        wins = d.get("wins", 0)
        wr = (wins / n * 100) if n > 0 else 0
        parts.append(f"{regime}: {n}trades WR={wr:.0f}%")
    return " | ".join(parts)

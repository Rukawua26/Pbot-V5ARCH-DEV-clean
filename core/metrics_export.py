import json
import os
import time

from core.bot_telemetry import collect_telemetry
from core.config.portable_paths import get_log_path


def _compute_trade_metrics(bot) -> dict:
    """Aggregate summary stats from active trades."""
    real_count = 0
    shadow_count = 0
    total_real_pnl = 0.0
    total_shadow_pnl = 0.0
    active_lock = getattr(bot, "lock", None)
    if active_lock:
        with active_lock:
            trades_snapshot = list(bot.active_trades.items())
    else:
        trades_snapshot = list(bot.active_trades.items())
    for sym, t in trades_snapshot:
        pnl = float(t.get("pnl", 0) or 0)
        if t.get("is_shadow"):
            shadow_count += 1
            total_shadow_pnl += pnl
        else:
            real_count += 1
            total_real_pnl += pnl
    return {
        "real_open_trades": real_count,
        "shadow_open_trades": shadow_count,
        "total_open_trades": real_count + shadow_count,
        "real_unrealized_pnl_usd": round(total_real_pnl, 2),
        "shadow_unrealized_pnl_usd": round(total_shadow_pnl, 2),
    }


def _compute_wallet_metrics(bot) -> dict:
    return {
        "balance_usd": round(float(getattr(bot, "balance", 0) or 0), 2),
        "available_balance_usd": round(float(getattr(bot, "available_balance", 0) or 0), 2),
    }


def _compute_system_metrics(bot) -> dict:
    return {
        "uptime_s": round(time.time() - float(getattr(bot, "_start_ts", time.time())), 2),
        "is_paused": bool(getattr(bot, "is_paused", False)),
        "halt_system_active": bool(getattr(bot, "halt_system_active", False)),
        "integrity_lock_active": bool(getattr(bot, "integrity_lock_active", False)),
        "circuit_breaker_active": bool(getattr(bot, "circuit_breaker_active", False)),
        "ml_healthy": getattr(bot, "ml_healthy", None),
    }


def export_metrics_summary(bot) -> dict:
    """Collect and write logs/metrics_summary.json with aggregated bot metrics."""
    try:
        path = get_log_path("metrics_summary.json")
        os.makedirs(path.parent, exist_ok=True)

        telemetry = collect_telemetry(bot, bot.log) if hasattr(bot, "log") else {}
        trade = _compute_trade_metrics(bot)
        wallet = _compute_wallet_metrics(bot)
        system = _compute_system_metrics(bot)

        summary = {
            "ts": telemetry.pop("ts", None) or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **wallet,
            **trade,
            **system,
            "telemetry": telemetry,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        return summary
    except Exception as error:
        bot.log(f"⚠️ Error exportando metrics summary: {error}")
        return {}

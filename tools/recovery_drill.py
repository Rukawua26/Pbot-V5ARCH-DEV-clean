#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.reconciliation import reconcile_bootstrap_state
from core.runtime_metrics import append_runtime_metric


class DrillBrain:
    def __init__(self):
        self.saved_states = []
        self.error_snapshots = []
        self.deleted_states = []

    def save_active_trade_state(self, symbol, state):
        self.saved_states.append((symbol, dict(state)))

    def save_error_snapshot(self, symbol, reason, payload):
        self.error_snapshots.append((symbol, reason, dict(payload or {})))

    def delete_active_trade_state(self, symbol):
        self.deleted_states.append(symbol)


class DrillExecution:
    def __init__(self, *, hard_sl_ok: bool = True):
        self._hard_sl_ok = hard_sl_ok
        self.place_hard_sl_calls = []
        self.fetch_positions_calls = 0
        self.fetch_open_orders_calls = 0

    def fetch_positions(self):
        self.fetch_positions_calls += 1
        return [
            {
                "symbol": "BTC/USDT:USDT",
                "contracts": 0.1,
                "side": "long",
                "entryPrice": 50000.0,
            }
        ]

    def fetch_open_orders(self):
        self.fetch_open_orders_calls += 1
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 49950.0}

    def place_hard_sl(self, symbol, side, amount, stop_price, client_order_id=None, params=None):
        self.place_hard_sl_calls.append(
            {
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "stop_price": stop_price,
                "client_order_id": client_order_id,
            }
        )
        if not self._hard_sl_ok:
            return None
        sl_side = "sell" if str(side).lower() == "buy" else "buy"
        return {
            "id": "drill-hard-sl-1",
            "symbol": symbol,
            "type": "STOP_MARKET",
            "side": sl_side,
            "amount": amount,
            "status": "open",
            "info": {"reduceOnly": True},
        }


class PositionsFailureExecution(DrillExecution):
    def fetch_positions(self):
        self.fetch_positions_calls += 1
        raise RuntimeError("fetch_positions ambiguous")


def _build_bot(execution=None) -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.lock = RLock()
    bot.db_lock = RLock()
    bot.active_trades = {}
    bot.balance = 1000.0
    bot.is_paused = False
    bot.integrity_lock_active = False
    bot.halt_system_active = False
    bot.instance_uuid = "recovery-drill"
    bot.execution = execution or DrillExecution()
    bot.brain = DrillBrain()
    bot.logs = []
    bot.log = bot.logs.append
    bot.get_current_balance = lambda: 1000.0
    return bot


def _run_with_real_config(bot) -> float:
    from core.reconciliation import Config

    original_paper_mode = Config.PAPER_MODE
    Config.PAPER_MODE = False
    started = time.perf_counter()
    try:
        reconcile_bootstrap_state(bot)
    finally:
        Config.PAPER_MODE = original_paper_mode
    return time.perf_counter() - started


def _scenario_orphan_adopted_after_hard_sl() -> dict:
    """Simulate restart recovery after the bot lost local state mid-trade."""
    bot = _build_bot()
    elapsed_s = _run_with_real_config(bot)

    trade = bot.active_trades.get("BTC/USDT") or {}
    hard_sl_calls = list(bot.execution.place_hard_sl_calls)
    safe = (
        trade.get("status") == "OPEN"
        and bool(trade.get("sl_exchange_order_id"))
        and len(hard_sl_calls) == 1
        and not bot.halt_system_active
    )
    return {
        "ok": safe,
        "scenario": "restart_with_exchange_orphan_adopted_after_hard_sl",
        "elapsed_seconds": round(elapsed_s, 6),
        "target_seconds": 30.0,
        "within_target": elapsed_s < 30.0,
        "active_trade_status": trade.get("status"),
        "sl_exchange_order_id": trade.get("sl_exchange_order_id"),
        "hard_sl_calls": len(hard_sl_calls),
        "fetch_positions_calls": bot.execution.fetch_positions_calls,
        "fetch_open_orders_calls": bot.execution.fetch_open_orders_calls,
        "halt_system_active": bool(bot.halt_system_active),
        "errors": list(bot.brain.error_snapshots),
    }


def _scenario_orphan_hard_sl_failure_halts() -> dict:
    bot = _build_bot(DrillExecution(hard_sl_ok=False))
    elapsed_s = _run_with_real_config(bot)
    trade = bot.active_trades.get("BTC/USDT") or {}
    safe_halt = (
        bool(bot.halt_system_active)
        and trade.get("status") == "ADOPTED_UNPROTECTED"
        and len(bot.execution.place_hard_sl_calls) == 1
    )
    return {
        "ok": safe_halt,
        "scenario": "restart_orphan_hard_sl_failure_halts",
        "elapsed_seconds": round(elapsed_s, 6),
        "target_seconds": 30.0,
        "within_target": elapsed_s < 30.0,
        "active_trade_status": trade.get("status"),
        "hard_sl_calls": len(bot.execution.place_hard_sl_calls),
        "halt_system_active": bool(bot.halt_system_active),
        "errors": list(bot.brain.error_snapshots),
    }


def _scenario_positions_failure_halts() -> dict:
    bot = _build_bot(PositionsFailureExecution())
    elapsed_s = _run_with_real_config(bot)
    safe_halt = bool(bot.halt_system_active) and bool(bot.integrity_lock_active)
    return {
        "ok": safe_halt,
        "scenario": "restart_fetch_positions_ambiguous_halts",
        "elapsed_seconds": round(elapsed_s, 6),
        "target_seconds": 30.0,
        "within_target": elapsed_s < 30.0,
        "fetch_positions_calls": bot.execution.fetch_positions_calls,
        "fetch_open_orders_calls": bot.execution.fetch_open_orders_calls,
        "halt_system_active": bool(bot.halt_system_active),
        "errors": list(bot.brain.error_snapshots),
    }


def run_recovery_drill() -> dict:
    scenarios = [
        _scenario_orphan_adopted_after_hard_sl(),
        _scenario_orphan_hard_sl_failure_halts(),
        _scenario_positions_failure_halts(),
    ]
    primary = dict(scenarios[0])
    primary["ok"] = all(row.get("ok") for row in scenarios)
    primary["within_target"] = all(row.get("within_target") for row in scenarios)
    primary["scenarios"] = scenarios
    primary["summary"] = {
        "scenarios": len(scenarios),
        "passed": sum(1 for row in scenarios if row.get("ok") and row.get("within_target")),
        "failed": sum(1 for row in scenarios if not (row.get("ok") and row.get("within_target"))),
    }
    append_runtime_metric(
        "recovery_drill",
        {
            "ok": primary["ok"],
            "within_target": primary["within_target"],
            "summary": primary["summary"],
        },
    )
    return primary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic recovery drill")
    parser.add_argument("--output", default="reports/recovery_drill_report.json")
    args = parser.parse_args()

    report = run_recovery_drill()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") and report.get("within_target") else 1


if __name__ == "__main__":
    raise SystemExit(main())

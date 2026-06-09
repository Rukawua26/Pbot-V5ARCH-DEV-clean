#!/usr/bin/env python3
import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import Config
from core.bot_guardian import run_guardian_loop
from core.bot_wallet_sync import sync_wallet
from core.execution_adapters import ShadowExecutionAdapter
from core.trade_manager import execute_order


class TimedRLock:
    def __init__(self):
        self._lock = threading.RLock()
        self.wait_over_100ms = 0
        self.max_wait_ms = 0.0

    def acquire(self, blocking=True, timeout=-1):
        started = time.perf_counter()
        ok = self._lock.acquire(blocking, timeout)
        wait_ms = (time.perf_counter() - started) * 1000.0
        if wait_ms > 100.0:
            self.wait_over_100ms += 1
        if wait_ms > self.max_wait_ms:
            self.max_wait_ms = wait_ms
        return ok

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


class SqliteStressBrain:
    def __init__(self, db_path: str, commit_delay_min: float = 0.0, commit_delay_max: float = 0.0):
        self.db_path = db_path
        self.commit_delay_min = max(0.0, float(commit_delay_min))
        self.commit_delay_max = max(self.commit_delay_min, float(commit_delay_max))
        self.sqlite_locked_errors = 0
        self.sqlite_backoff_executions = 0
        self.sqlite_hard_failures = 0
        self.db_write_over_100ms = 0
        self.db_write_max_ms = 0.0
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_trades_state (
                symbol TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                reason TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()

    def _commit(self, conn):
        if self.commit_delay_max > 0.0:
            time.sleep(random.uniform(self.commit_delay_min, self.commit_delay_max))
        conn.commit()

    def _with_backoff(self, write_fn):
        for attempt in range(3):
            try:
                return write_fn()
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower():
                    self.sqlite_locked_errors += 1
                    if attempt < 2:
                        self.sqlite_backoff_executions += 1
                        time.sleep(0.05 * (2**attempt))
                        continue
                break
        self.sqlite_hard_failures += 1
        return False

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=1.0)

    def _track_write_duration(self, started: float):
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > 100.0:
            self.db_write_over_100ms += 1
        if elapsed_ms > self.db_write_max_ms:
            self.db_write_max_ms = elapsed_ms

    def get_genetic_params(self, _symbol):
        return {}

    def get_stats_by_trend(self):
        return {}

    def save_active_trade_state(self, symbol, state):
        started = time.perf_counter()
        def _write():
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO active_trades_state(symbol, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET state_json=excluded.state_json, updated_at=CURRENT_TIMESTAMP
                """,
                (symbol, json.dumps(state, default=str, ensure_ascii=False)),
            )
            self._commit(conn)
            conn.close()
            self._track_write_duration(started)
            return True

        return bool(self._with_backoff(_write))

    def delete_active_trade_state(self, symbol):
        started = time.perf_counter()
        def _write():
            conn = self._conn()
            conn.execute("DELETE FROM active_trades_state WHERE symbol=?", (symbol,))
            self._commit(conn)
            conn.close()
            self._track_write_duration(started)
            return True

        return bool(self._with_backoff(_write))

    def save_error_snapshot(self, symbol, reason, ctx):
        started = time.perf_counter()
        def _write():
            conn = self._conn()
            conn.execute(
                "INSERT INTO error_log(symbol, reason, payload) VALUES (?, ?, ?)",
                (symbol, reason, json.dumps(ctx or {}, ensure_ascii=False)),
            )
            self._commit(conn)
            conn.close()
            self._track_write_duration(started)
            return True

        return bool(self._with_backoff(_write))


class _LiveTickerStub:
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._prices = {}
        self.logger = SimpleNamespace(
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
        )
        self.exchange = object()

    def _price(self, symbol: str) -> float:
        p = self._prices.get(symbol, 100.0 + self._rng.uniform(-10, 10))
        p = max(1.0, p * (1.0 + self._rng.uniform(-0.0015, 0.0015)))
        self._prices[symbol] = p
        return p

    def fetch_ticker(self, symbol: str):
        return {"last": self._price(symbol)}

    def set_leverage(self, _lev, _symbol):
        return {"ok": True}

    def fetch_open_orders(self, _symbol=None):
        return []

    def fetch_order_by_client_id(self, _symbol, _coid):
        return None


def _build_bot(execution, brain):
    bot = SimpleNamespace()
    bot.lock = threading.RLock()
    bot.db_lock = TimedRLock()
    bot.logs = []

    def _log(msg):
        bot.logs.append(msg)

    bot.log = _log
    bot.is_running = True
    bot.integrity_lock_active = False
    bot.halt_system_active = False
    bot.balance = 10_000.0
    bot.available_balance = 10_000.0
    bot.is_paused = False
    bot.circuit_breaker_active = False
    bot.cooldown_pairs = {}
    bot.active_trades = {}
    bot._guardian_stats = {
        "loops": 0,
        "work_s": 0.0,
        "sleep_s": 0.0,
        "bailout_count": 0,
    }
    bot._exit_eval_last_log = {}
    bot.price_lock = threading.RLock()
    bot.live_prices = {}
    bot.monitor_open_trades = lambda: None
    bot.sync_wallet = lambda: None
    bot.close_trade = lambda *_a, **_k: None
    bot.abort_partial_trade = lambda *_a, **_k: None
    bot.is_hedge_mode = False
    bot.ghost_model = None
    bot.ghost_model_type = None
    bot.instance_uuid = "stress-injector"
    bot._symbol_reduced_size_mult = 1.0
    bot.market_btc_change_tf = 0.0
    bot._load_runtime_symbol_controls = lambda: {"blocked": set(), "reduced": set()}
    bot._get_base_coin = lambda s: s.split("/")[0]
    bot.get_current_balance = lambda: 10_000.0
    bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})
    bot.brain = brain
    bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
    bot.risk_engine = SimpleNamespace(
        calculate_position_size=lambda **kwargs: (1.0, 150.0),
        get_exit_levels=lambda **kwargs: (
            kwargs.get("entry_price", 100.0) * 0.99,
            kwargs.get("entry_price", 100.0) * 1.02,
            "STD",
        ),
        check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        should_abort_trade=lambda *_args, **_kwargs: (False, ""),
    )
    bot.exit_engine = SimpleNamespace(
        evaluate_exit=lambda **_kwargs: {"should_exit": False, "reason": "NOOP"}
    )
    bot.execution = execution
    return bot


def _load_events_since(start_ts: float):
    out = []
    try:
        with open("logs/execution_events.jsonl", "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ts_str = str(rec.get("ts") or "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                if ts >= start_ts:
                    out.append(rec)
    except FileNotFoundError:
        return []
    return out


def _collect_pending_states(db_path: str):
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT symbol, state_json FROM active_trades_state").fetchall()
    conn.close()
    counts = {"PENDING_SEND": 0, "PENDING_EXCHANGE_OPEN": 0}
    for _symbol, state_json in rows:
        try:
            state = json.loads(state_json)
        except Exception:
            continue
        status = str((state or {}).get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def _build_timeline(events, symbol: str):
    symbol_events = [
        e for e in events if str((e.get("payload") or {}).get("symbol") or "") == symbol
    ]
    symbol_events.sort(key=lambda e: str(e.get("ts") or ""))
    t0 = next(
        (e for e in symbol_events if e.get("event") == "PENDING_SEND_PERSISTED"), None
    )
    t1 = next((e for e in symbol_events if e.get("event") == "ENTRY_ORDER_ACK"), None)
    t2 = next(
        (e for e in symbol_events if e.get("event") == "PARTIAL_FILL_DETECTED"),
        None,
    )
    t3 = next(
        (e for e in symbol_events if e.get("event") == "GUARDIAN_PARTIAL_OBSERVED"),
        None,
    )
    return {"T0": t0, "T1": t1, "T2": t2, "T3": t3, "all": symbol_events}


def main():
    parser = argparse.ArgumentParser(
        description="Inyector de estrés para TradeManager + Shadow adapter"
    )
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--orders-per-minute", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--commit-delay-min", type=float, default=0.0)
    parser.add_argument("--commit-delay-max", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--symbol-focus", type=str, default="BTC/USDT")
    args = parser.parse_args()

    Config.PAPER_MODE = False
    os.environ["TRADE_COOLDOWN_MINUTES"] = "0"
    Config.PARTIAL_FILL_TIMEOUT_SECONDS = 30
    setattr(Config, "TRADE_COOLDOWN_MINUTES", 0)
    setattr(Config, "GLOBAL_ENTRY_COOLDOWN_SECONDS", 0)
    setattr(Config, "SIGNAL_COOLDOWN_SHADOW_SECONDS", 0)
    setattr(Config, "MAX_SECTOR_EXPOSURE", 10000)
    setattr(Config, "MAX_OPEN_TRADES", 10000)
    setattr(Config, "MAX_DIRECTIONAL_TRADES", 10000)
    setattr(Config, "REQUIRE_GHOST_MODEL_FOR_TRADING", False)

    rng = random.Random(args.seed)
    live_stub = _LiveTickerStub(seed=args.seed)
    shadow_exec = ShadowExecutionAdapter(
        live_stub,
        min_latency_ms=200,
        max_latency_ms=500,
        reject_rate=0.08,
        partial_fill_rate=1.0,
        partial_fill_complete_rate=0.55,
        min_partial_ratio=0.25,
    )

    db_path = os.path.join(ROOT_DIR, "logs", "shadow_stress.db")
    brain = SqliteStressBrain(
        db_path,
        commit_delay_min=args.commit_delay_min,
        commit_delay_max=args.commit_delay_max,
    )
    bot = _build_bot(shadow_exec, brain)

    guardian_thread = threading.Thread(
        target=run_guardian_loop, args=(bot,), daemon=True
    )
    guardian_thread.start()

    symbols = [args.symbol_focus] + [f"S{i}/USDT" for i in range(1, 2000)]
    interval = max(0.01, 60.0 / max(1, args.orders_per_minute))
    total_orders = max(1, int(args.minutes * args.orders_per_minute))

    start = time.time()
    results = []
    stop_sync = threading.Event()

    def _sync_loop():
        while not stop_sync.is_set():
            try:
                sync_wallet(bot)
            except Exception as error:
                bot.log(f"sync_loop_error: {error}")
            time.sleep(0.35)

    sync_thread = threading.Thread(target=_sync_loop, daemon=True)
    sync_thread.start()

    def _fire_once(i: int):
        symbol = symbols[i] if i < len(symbols) else f"SX{i}/USDT"
        price = float(live_stub.fetch_ticker(symbol)["last"])
        t0 = time.perf_counter()
        result = execute_order(
            bot,
            symbol=symbol,
            side="BUY" if rng.random() > 0.5 else "SELL",
            price=price,
            atr=max(0.1, price * 0.01),
            is_shadow=False,
            context={"trend": "RANGO", "spread": 0.0002, "prob_final": 76.0},
        )
        dt = time.perf_counter() - t0
        return {"symbol": symbol, "result": result, "latency_s": round(dt, 6)}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = []
        next_tick = time.perf_counter()
        for i in range(total_orders):
            futures.append(pool.submit(_fire_once, i))
            if i > 0 and i % max(1, args.orders_per_minute) == 0:
                elapsed_progress = time.time() - start
                print(
                    f"[progress] sent={i}/{total_orders} elapsed_s={elapsed_progress:.1f}",
                    flush=True,
                )
            next_tick += interval
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
        for fut in futures:
            results.append(fut.result())

    time.sleep(1.5)
    stop_sync.set()
    sync_thread.join(timeout=2.0)
    bot.is_running = False
    guardian_thread.join(timeout=2.0)

    elapsed = time.time() - start
    events = _load_events_since(start)
    ack = [e for e in events if e.get("event") == "ENTRY_ORDER_ACK"]
    partial_timeout = [
        e for e in events if e.get("event") == "PARTIAL_FILL_TIMEOUT_CANCEL"
    ]
    rejects = [
        r
        for r in results
        if str(r.get("result", "")).startswith("EXECUTION")
        or "FAIL" in str(r.get("result", ""))
    ]

    pending_counts = _collect_pending_states(db_path)
    partial_symbols = [
        str((e.get("payload") or {}).get("symbol") or "")
        for e in events
        if e.get("event") == "PARTIAL_FILL_DETECTED"
    ]
    focus_symbol = partial_symbols[0] if partial_symbols else args.symbol_focus
    timeline = _build_timeline(events, focus_symbol)

    print("=== SHADOW STRESS SUMMARY ===")
    print(f"started_at={datetime.fromtimestamp(start, UTC).isoformat()}")
    print(
        f"orders_sent={total_orders} elapsed_s={elapsed:.2f} rate={total_orders / max(elapsed, 1e-6):.2f}/s"
    )
    print(f"entry_ack_events={len(ack)} partial_timeout_cancel={len(partial_timeout)}")
    print(f"raw_reject_like_results={len(rejects)}")
    print(f"active_trades_end={len(bot.active_trades)}")

    avg_slippage = 0.0
    if ack:
        avg_slippage = sum(
            float((ev.get("payload") or {}).get("slippage_simulated") or 0.0)
            for ev in ack
        ) / len(ack)
    print(f"avg_slippage_simulated={avg_slippage:.6f}")

    print("=== METRIC 1: DB LOCK CONTENTION ===")
    print(f"db_lock_wait_over_100ms={bot.db_lock.wait_over_100ms}")
    print(f"db_lock_max_wait_ms={bot.db_lock.max_wait_ms:.3f}")
    print(f"db_write_commit_over_100ms={brain.db_write_over_100ms}")
    print(f"db_write_commit_max_ms={brain.db_write_max_ms:.3f}")
    print(f"sqlite_database_locked_errors={brain.sqlite_locked_errors}")
    print(f"sqlite_backoff_executions={brain.sqlite_backoff_executions}")
    print(f"sqlite_hard_failures={brain.sqlite_hard_failures}")
    print(
        f"sqlite_commit_delay_range_s={brain.commit_delay_min:.3f}-{brain.commit_delay_max:.3f}"
    )

    print("=== METRIC 2: TIMELINE FOCUS ===")
    for label in ("T0", "T1", "T2", "T3"):
        ev = timeline.get(label)
        if not ev:
            print(f"{label}=null")
            continue
        print(
            f"{label}={ev.get('ts')} event={ev.get('event')} payload={json.dumps(ev.get('payload') or {}, ensure_ascii=False)}"
        )
    print(
        f"timeline_focus_symbol={focus_symbol} total_events={len(timeline.get('all', []))}"
    )

    print("=== METRIC 3: PENDING LEAKAGE ===")
    print(f"pending_send_unresolved={pending_counts.get('PENDING_SEND', 0)}")
    print(
        f"pending_exchange_open_unresolved={pending_counts.get('PENDING_EXCHANGE_OPEN', 0)}"
    )


if __name__ == "__main__":
    main()

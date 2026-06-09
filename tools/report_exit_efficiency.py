#!/usr/bin/env python3
"""
Reporte de eficiencia de salidas (Exit Engine v118).

Tabla por razón de salida:
- Cantidad
- PnL Medio (%)
- MFE Medio (%)
- Profit Factor
- Deriva post-exit (subió/bajó después)
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


EXIT_REASONS = [
    "STRUCTURAL_INVALIDATION",
    "TIME_DECAY_ESCAPE_VELOCITY",
    "ATR_TRAILING_HIT",
]


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def map_reason(reason: str) -> Optional[str]:
    r = (reason or "").upper().strip()
    for k in EXIT_REASONS:
        if k in r:
            return k
    return None


def load_df(candle_dir: Path, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    p = candle_dir / f"{symbol.replace('/', '_')}_{timeframe}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    req = {"time", "high", "low"}
    if not req.issubset(set(df.columns)):
        return None
    return (
        df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    )


def post_exit_drift_pct(
    df: pd.DataFrame,
    side: str,
    exit_ts_iso: str,
    exit_price: float,
    lookahead_bars: int,
) -> Optional[float]:
    if not exit_ts_iso or exit_price <= 0:
        return None
    try:
        ts = pd.to_datetime(exit_ts_iso).timestamp() * 1000
    except Exception:
        return None

    idx = df[df["time"] >= ts].index
    if len(idx) == 0:
        return None
    i0 = int(idx[0])
    i1 = min(len(df) - 1, i0 + max(1, lookahead_bars))
    w = df.iloc[i0 : i1 + 1]
    if w.empty:
        return None

    if side == "BUY":
        best = float(w["high"].max())
        drift = ((best - exit_price) / exit_price) * 100.0
    else:
        best = float(w["low"].min())
        drift = ((exit_price - best) / exit_price) * 100.0
    return float(drift)


def main() -> None:
    p = argparse.ArgumentParser(description="Reporte de eficiencia de salidas")
    p.add_argument("--db", default="sniper_brain.db")
    p.add_argument("--timeframe", default="1h", choices=["15m", "1h"])
    p.add_argument("--lookahead-bars", type=int, default=4)
    p.add_argument("--include-shadow", action="store_true")
    p.add_argument("--include-real", action="store_true")
    p.add_argument("--candles", default="data_storage/candles")
    args = p.parse_args()

    if not args.include_shadow and not args.include_real:
        args.include_shadow = True

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"DB no encontrada: {db}")

    where_modes: List[str] = []
    if args.include_shadow:
        where_modes.append("is_shadow = 1")
    if args.include_real:
        where_modes.append("is_shadow = 0")
    mode_sql = " OR ".join(where_modes)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    q = f"""
    SELECT id, timestamp, symbol, side, exit_price, pnl_percent, mfe_percent, reason, is_shadow
    FROM trades
    WHERE ({mode_sql})
      AND (reason IS NOT NULL AND reason != '')
    ORDER BY id DESC
    """
    rows = conn.execute(q).fetchall()
    conn.close()

    candle_dir = Path(args.candles)
    cache: Dict[str, Optional[pd.DataFrame]] = {}

    buckets: Dict[str, Dict[str, float]] = {
        r: {
            "n": 0,
            "pnl_sum": 0.0,
            "mfe_sum": 0.0,
            "gp": 0.0,
            "gl": 0.0,
            "post_sum": 0.0,
            "post_n": 0,
        }
        for r in EXIT_REASONS
    }

    for row in rows:
        reason_key = map_reason(str(row["reason"]))
        if reason_key is None:
            continue

        side = str(row["side"] or "BUY")
        symbol = str(row["symbol"])
        pnl = float(row["pnl_percent"] or 0.0)
        mfe = float(row["mfe_percent"] or 0.0)

        b = buckets[reason_key]
        b["n"] += 1
        b["pnl_sum"] += pnl
        b["mfe_sum"] += mfe
        if pnl > 0:
            b["gp"] += pnl
        else:
            b["gl"] += pnl

        if symbol not in cache:
            cache[symbol] = load_df(candle_dir, symbol, args.timeframe)
        df = cache[symbol]
        if df is not None:
            drift = post_exit_drift_pct(
                df=df,
                side=side,
                exit_ts_iso=str(row["timestamp"]),
                exit_price=float(row["exit_price"] or 0.0),
                lookahead_bars=args.lookahead_bars,
            )
            if drift is not None:
                b["post_sum"] += drift
                b["post_n"] += 1

    print("=== EXIT EFFICIENCY REPORT v118 ===")
    mode_txt = []
    if args.include_shadow:
        mode_txt.append("SHADOW")
    if args.include_real:
        mode_txt.append("REAL")
    print(
        f"Modo: {', '.join(mode_txt)} | TF={args.timeframe} | lookahead={args.lookahead_bars}"
    )
    print()
    print(
        "Razón                        Cantidad   PnL Medio%   MFE Medio%   ProfitFactor   PostExitDrift%"
    )
    print(
        "-----------------------------------------------------------------------------------------------"
    )

    for r in EXIT_REASONS:
        b = buckets[r]
        n = int(b["n"])
        pnl_mean = safe_div(b["pnl_sum"], n)
        mfe_mean = safe_div(b["mfe_sum"], n)
        pf = safe_div(b["gp"], abs(b["gl"]))
        post = safe_div(b["post_sum"], int(b["post_n"]))
        print(
            f"{r:28} {n:8d} {pnl_mean:11.4f} {mfe_mean:11.4f} {pf:13.4f} {post:14.4f}"
        )


if __name__ == "__main__":
    main()

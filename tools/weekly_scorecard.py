#!/usr/bin/env python3
"""
Scorecard semanal (limpio) para shadow trades.

Excluye por defecto registros basura VETO_ERROR (-99%).
Calcula:
- Win Rate
- Avg Win / Avg Loss
- Profit Factor
- Expectancy (%)
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path


EXCLUDE_GARBAGE = """
NOT (
    (COALESCE(side, '') = 'VETO_ERROR' OR COALESCE(reason, '') = 'VETO_ERROR')
    AND COALESCE(pnl_percent, 0) <= -98.0
)
"""


@dataclass
class Metrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win: float
    avg_loss: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    expectancy_pct: float


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def compute_metrics(conn: sqlite3.Connection, where_sql: str) -> Metrics:
    cur = conn.cursor()
    q = f"""
    SELECT
      COUNT(*) AS trades,
      SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) AS wins,
      SUM(CASE WHEN pnl_percent <= 0 THEN 1 ELSE 0 END) AS losses,
      AVG(CASE WHEN pnl_percent > 0 THEN pnl_percent END) AS avg_win,
      AVG(CASE WHEN pnl_percent <= 0 THEN pnl_percent END) AS avg_loss,
      SUM(CASE WHEN pnl_percent > 0 THEN pnl_percent ELSE 0 END) AS gross_profit,
      SUM(CASE WHEN pnl_percent <= 0 THEN pnl_percent ELSE 0 END) AS gross_loss
    FROM trades
    WHERE is_shadow = 1 AND {where_sql}
    """
    cur.execute(q)
    row = cur.fetchone() or (0, 0, 0, None, None, None, None)

    trades = int(row[0] or 0)
    wins = int(row[1] or 0)
    losses = int(row[2] or 0)
    avg_win = float(row[3] or 0.0)
    avg_loss = float(row[4] or 0.0)
    gross_profit = float(row[5] or 0.0)
    gross_loss = float(row[6] or 0.0)

    win_rate = safe_div(wins, trades) * 100.0
    profit_factor = safe_div(gross_profit, abs(gross_loss))
    p_win = safe_div(wins, trades)
    p_loss = safe_div(losses, trades)
    expectancy_pct = (p_win * avg_win) + (p_loss * avg_loss)

    return Metrics(
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        expectancy_pct=expectancy_pct,
    )


def print_metrics(title: str, m: Metrics) -> None:
    print(title)
    print(f"Trades:        {m.trades}")
    print(f"Wins/Losses:   {m.wins}/{m.losses}")
    print(f"Win Rate:      {m.win_rate:.2f}%")
    print(f"Avg Win:       {m.avg_win:+.4f}%")
    print(f"Avg Loss:      {m.avg_loss:+.4f}%")
    print(f"Profit Factor: {m.profit_factor:.4f}")
    print(f"Expectancy:    {m.expectancy_pct:+.4f}% por trade")
    print(f"Gross P/L:     +{m.gross_profit:.4f}% / {m.gross_loss:.4f}%")
    print()


def weekly_breakdown(conn: sqlite3.Connection, where_sql: str, weeks: int) -> None:
    cur = conn.cursor()
    q = f"""
    SELECT
      strftime('%Y-W%W', replace(timestamp, 'T', ' ')) AS yw,
      COUNT(*) AS trades,
      SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) AS wins,
      AVG(CASE WHEN pnl_percent > 0 THEN pnl_percent END) AS avg_win,
      AVG(CASE WHEN pnl_percent <= 0 THEN pnl_percent END) AS avg_loss,
      SUM(CASE WHEN pnl_percent > 0 THEN pnl_percent ELSE 0 END) AS gross_profit,
      SUM(CASE WHEN pnl_percent <= 0 THEN pnl_percent ELSE 0 END) AS gross_loss
    FROM trades
    WHERE is_shadow = 1 AND {where_sql}
    GROUP BY yw
    ORDER BY yw DESC
    LIMIT ?
    """
    cur.execute(q, (weeks,))
    rows = cur.fetchall()

    print("=== Scorecard semanal (limpio) ===")
    print("Semana      Trades   WR%    AvgWin%   AvgLoss%  PF      Expect%")
    print("----------------------------------------------------------------")
    for r in rows:
        yw = r[0] or "N/A"
        trades = int(r[1] or 0)
        wins = int(r[2] or 0)
        avg_win = float(r[3] or 0.0)
        avg_loss = float(r[4] or 0.0)
        gp = float(r[5] or 0.0)
        gl = float(r[6] or 0.0)
        losses = max(0, trades - wins)
        wr = safe_div(wins, trades) * 100.0
        pf = safe_div(gp, abs(gl))
        expectancy = (safe_div(wins, trades) * avg_win) + (
            safe_div(losses, trades) * avg_loss
        )
        print(
            f"{yw:10} {trades:6d} {wr:6.2f} {avg_win:9.4f} {avg_loss:10.4f} {pf:7.4f} {expectancy:9.4f}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Scorecard semanal shadow")
    p.add_argument("--db", default="sniper_brain.db")
    p.add_argument("--weeks", type=int, default=8)
    p.add_argument("--include-garbage", action="store_true")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB no encontrada: {db_path}")

    where_sql = "1=1" if args.include_garbage else EXCLUDE_GARBAGE
    conn = sqlite3.connect(str(db_path))

    clean_metrics = compute_metrics(conn, where_sql)
    print_metrics("=== Resumen global ===", clean_metrics)
    weekly_breakdown(conn, where_sql, args.weeks)

    conn.close()


if __name__ == "__main__":
    main()

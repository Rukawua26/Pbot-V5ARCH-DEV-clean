#!/usr/bin/env python3
"""
Snapshot de estado táctico del bot.

Muestra:
1) Pares en ACECHO (desde logs recientes)
2) Trades activos (desde active_trades_state)
3) Profit Factor últimas 24h (shadow)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ACECHO_RE = re.compile(
    r"\[ACECHO(?::(?P<source>[A-Z_]+))?\]\s+(?P<symbol>[A-Z0-9/]+)\s+side=(?P<side>BUY|SELL)\s+IA=(?P<ia>[0-9.]+)%\s+shock=(?P<shock>[0-9.]+)\s+dist=(?P<dist>[0-9.]+)%"
)


def safe_div(a, b):
    try:
        return float(a) / float(b) if float(b) != 0 else 0.0
    except Exception:
        return 0.0


def load_recent_acecho(log_path: Path, limit: int = 20):
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-4000:]
    seen = {}
    for ln in lines:
        m = ACECHO_RE.search(ln)
        if not m:
            continue
        sym = m.group("symbol")
        seen[sym] = {
            "symbol": sym,
            "side": m.group("side"),
            "ia": float(m.group("ia")),
            "shock": float(m.group("shock")),
            "dist": float(m.group("dist")),
            "source": m.group("source") or "SHOCK",
        }
    vals = sorted(seen.values(), key=lambda x: x["ia"], reverse=True)
    return vals[:limit]


def load_active_trades(conn: sqlite3.Connection):
    rows = conn.execute("SELECT symbol, state_data FROM active_trades_state").fetchall()
    out = []
    for r in rows:
        try:
            st = json.loads(r[1])
        except Exception:
            continue
        side = st.get("side", "BUY")
        pnl = float(st.get("pnl", 0.0) or 0.0)
        peak = float(st.get("peak_pnl", pnl) or pnl)
        entry = float(st.get("entry", 0.0) or 0.0)
        last_price = float(st.get("last_price", 0.0) or 0.0)
        mfe_price = float(st.get("mfe_price", entry) or entry)
        shock = st.get("entry_shock_level")

        mfe_pct = 0.0
        if entry > 0:
            if side == "BUY":
                mfe_pct = ((mfe_price - entry) / entry) * 100.0
            else:
                mfe_pct = ((entry - mfe_price) / entry) * 100.0

        latent_reason = "HOLD"
        trailing_gap = max(0.0, peak - pnl)
        if trailing_gap > 0.6:
            latent_reason = "NEAR_ATR_TRAILING"

        if shock is not None and last_price > 0:
            shock = float(shock)
            struct_dist = abs(last_price - shock) / last_price * 100.0
            if struct_dist < 0.4:
                latent_reason = "NEAR_STRUCTURAL_INVALIDATION"
        else:
            struct_dist = None

        out.append(
            {
                "symbol": r[0],
                "side": side,
                "pnl": pnl,
                "mfe": mfe_pct,
                "latent": latent_reason,
                "struct_dist": struct_dist,
                "trailing_gap": trailing_gap,
            }
        )
    return out


def session_pf_24h(conn: sqlite3.Connection):
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        """
        SELECT pnl_percent FROM trades
        WHERE is_shadow = 1 AND timestamp >= ?
        """,
        (since,),
    ).fetchall()
    gp = sum(float(r[0] or 0.0) for r in rows if float(r[0] or 0.0) > 0)
    gl = sum(float(r[0] or 0.0) for r in rows if float(r[0] or 0.0) <= 0)
    pf = safe_div(gp, abs(gl))
    return len(rows), pf


def main():
    ap = argparse.ArgumentParser(description="Snapshot táctico del bot")
    ap.add_argument("--db", default="sniper_brain.db")
    ap.add_argument("--log", default="sniper.log")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB no encontrada: {db_path}")

    conn = sqlite3.connect(str(db_path))
    acecho = load_recent_acecho(Path(args.log))
    active = load_active_trades(conn)
    n24, pf24 = session_pf_24h(conn)
    conn.close()

    print("=== BOT STATUS SNAPSHOT ===")
    print()
    print("[ACECHO]")
    if not acecho:
        print("(vacío)")
    else:
        print("Symbol           Side   IA%   Dist%   Source")
        print("----------------------------------------------")
        for x in acecho:
            print(
                f"{x['symbol'][:15]:15} {x['side']:5} {x['ia']:5.1f} {x['dist']:7.2f} {x['source'][:12]}"
            )

    print()
    print("[TRADES ACTIVOS]")
    if not active:
        print("(ninguno)")
    else:
        print("Symbol           Side    PnL%    MFE%    Latent Exit")
        print("---------------------------------------------------------------")
        for t in active:
            print(
                f"{t['symbol'][:15]:15} {t['side']:5} {t['pnl']:7.2f} {t['mfe']:7.2f} {t['latent']}"
            )

    print()
    print("[SESIÓN 24H]")
    print(f"Trades shadow: {n24}")
    print(f"Profit Factor: {pf24:.4f}")


if __name__ == "__main__":
    main()

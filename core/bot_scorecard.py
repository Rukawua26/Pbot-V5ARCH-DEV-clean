import sqlite3
import time
from datetime import datetime, timedelta

from tools.notifier import send_telegram_msg


def send_daily_exit_scorecard(bot):
    try:
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        conn = sqlite3.connect(bot.brain.db_name)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, timestamp, symbol, side, exit_price, pnl_percent, mfe_percent, reason, exit_reason
            FROM trades
            WHERE is_shadow = 1
              AND timestamp >= ?
              AND timestamp < ?
            ORDER BY id DESC
            """,
            (day_start.isoformat(), day_end.isoformat()),
        ).fetchall()
        real_rows = conn.execute(
            """
            SELECT pnl_percent
            FROM trades
            WHERE is_shadow = 0
              AND timestamp >= ?
              AND timestamp < ?
            """,
            (day_start.isoformat(), day_end.isoformat()),
        ).fetchall()
        conn.close()

        reason_keys = [
            "STRUCTURAL_INVALIDATION",
            "TIME_DECAY_ESCAPE_VELOCITY",
            "ATR_TRAILING_HIT",
            "OTHER",
        ]
        buckets = {
            key: {
                "n": 0,
                "pnl": 0.0,
                "mfe": 0.0,
                "gp": 0.0,
                "gl": 0.0,
                "drift": 0.0,
                "drift_n": 0,
            }
            for key in reason_keys
        }

        def map_reason(row):
            raw_reason = str(row["reason"] or "")
            normalized_reason = str(row["exit_reason"] or "")
            for reason_key in reason_keys[:-1]:
                if reason_key in raw_reason or reason_key == normalized_reason:
                    return reason_key
            return "OTHER"

        total_trades = len(rows)
        wins = 0
        losses = 0
        avg_win_sum = 0.0
        avg_loss_sum = 0.0
        gp_all = 0.0
        gl_all = 0.0

        for row in rows:
            key = map_reason(row)
            pnl = float(row["pnl_percent"] or 0.0)
            mfe = float(row["mfe_percent"] or 0.0)
            if pnl > 0:
                wins += 1
                avg_win_sum += pnl
                gp_all += pnl
            else:
                losses += 1
                avg_loss_sum += pnl
                gl_all += pnl

            bucket = buckets[key]
            bucket["n"] += 1
            bucket["pnl"] += pnl
            bucket["mfe"] += mfe
            if pnl > 0:
                bucket["gp"] += pnl
            else:
                bucket["gl"] += pnl

            drift = bot._calc_post_exit_drift(
                symbol=str(row["symbol"]),
                side=str(row["side"]),
                exit_ts_iso=str(row["timestamp"]),
                exit_price=float(row["exit_price"] or 0.0),
                lookahead_bars=4,
            )
            if drift is not None:
                bucket["drift"] += float(drift)
                bucket["drift_n"] += 1

        wr = bot._safe_div(wins, total_trades) * 100.0
        avg_win = bot._safe_div(avg_win_sum, wins)
        avg_loss = bot._safe_div(avg_loss_sum, losses)
        expectancy = (bot._safe_div(wins, total_trades) * avg_win) + (
            bot._safe_div(losses, total_trades) * avg_loss
        )
        pf_all = bot._safe_div(gp_all, abs(gl_all))

        real_total = len(real_rows)
        real_wins = sum(1 for row in real_rows if float(row["pnl_percent"] or 0.0) > 0)
        real_pnl = sum(float(row["pnl_percent"] or 0.0) for row in real_rows)
        real_wr = bot._safe_div(real_wins, real_total) * 100.0

        watchlist = list(bot.breakout_agent.watchlist.keys())[:8]
        wl_txt = ", ".join(watchlist) if watchlist else "vacía"
        wl_sources = bot.breakout_agent.summary_by_source()
        wl_sources_txt = " | ".join(
            [f"{key}:{int(value)}" for key, value in sorted(wl_sources.items())]
        )
        if not wl_sources_txt:
            wl_sources_txt = "N/A"

        def row_text(label, key):
            bucket = buckets[key]
            n = int(bucket["n"])
            pnl_m = bot._safe_div(bucket["pnl"], n)
            mfe_m = bot._safe_div(bucket["mfe"], n)
            pf = bot._safe_div(bucket["gp"], abs(bucket["gl"]))
            drift_m = bot._safe_div(bucket["drift"], int(bucket["drift_n"]))
            return (
                f"{label}\n"
                f"- Qty: {n} | PnL: {pnl_m:+.2f}% | MFE: {mfe_m:.2f}% | Drift: {drift_m:+.2f}%\n"
                f"- PF: {pf:.2f}"
            )

        message = (
            "📊 *SCORECARD DIARIO DE EFICIENCIA (v118)*\n"
            "---------------------------------------\n"
            f"Fecha: {day_start.strftime('%Y-%m-%d')}\n"
            f"Total Trades (Shadow): {total_trades}\n"
            f"Win Rate: {wr:.2f}%\n"
            f"Expectancy: {expectancy:+.4f}%\n"
            f"Profit Factor: {pf_all:.2f}\n\n"
            "🚀 *REAL HOY:*\n"
            f"- Trades: {real_total} | WR: {real_wr:.2f}% | PnL: {real_pnl:+.2f}%\n\n"
            "🧪 *DESGLOSE POR SALIDA:*\n"
            f"1) INVALIDACIÓN (Structural)\n{row_text('', 'STRUCTURAL_INVALIDATION')}\n\n"
            f"2) ESCAPE (Time Decay)\n{row_text('', 'TIME_DECAY_ESCAPE_VELOCITY')}\n\n"
            f"3) CAZA (ATR Trailing)\n{row_text('', 'ATR_TRAILING_HIT')}\n\n"
            f"4) OTRAS SALIDAS\n{row_text('', 'OTHER')}\n\n"
            "🚀 *BREAKOUTS:*\n"
            f"- Watchlist actual: {wl_txt}\n"
            f"- Fuentes watchlist: {wl_sources_txt}\n"
            f"- Overrides Shadow ejecutados: {int(bot.breakout_overrides_today)}"
        )
        send_telegram_msg(message)
    except Exception as error:
        bot.log(f"⚠️ Error scorecard diario: {error}")


def maybe_send_daily_exit_scorecard(bot):
    try:
        now_ts = time.time()
        if now_ts >= float(bot._daily_report_next_ts):
            send_daily_exit_scorecard(bot)
            bot._daily_report_next_ts = now_ts + 24 * 3600
            bot.breakout_overrides_today = 0
    except Exception as error:
        bot.log(f"⚠️ Error chequeando scorecard diario: {error}")

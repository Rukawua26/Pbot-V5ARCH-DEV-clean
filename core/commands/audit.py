import platform
from collections import Counter
from pathlib import Path

from config import Config
from tools.notifier import send_telegram_msg


def _handle_audit_commands(bot, text: str) -> bool:
    if text == "/audit" or text == "/report100":
        send_telegram_msg("🔍 *GENERANDO REPORTE DE AUDITORÍA...*")
        try:
            with bot.db_lock:
                trades = bot.brain.get_last_n_trades(100)
            from tools.reporter import generate_audit_report

            report = generate_audit_report(trades)
            send_telegram_msg(report)
        except Exception as error:
            send_telegram_msg(f"❌ Error generando auditoría: {error}")
        return True

    if text == "/audit_report":
        send_telegram_msg("🔍 *GENERANDO REPORTE DE AUDITORÍA (Últimos 100)...*")
        try:
            with bot.db_lock:
                trades = bot.brain.get_last_n_trades(100)

            if not trades:
                send_telegram_msg("No hay trades para auditar.")
                return True

            wins = sum(1 for trade in trades if trade["pnl_percent"] > 0)
            losses = len(trades) - wins
            win_rate = (wins / len(trades)) * 100 if trades else 0
            total_pnl = sum(trade["pnl_percent"] for trade in trades)
            avg_pnl = total_pnl / len(trades) if trades else 0

            real_trades = [trade for trade in trades if not trade.get("is_shadow")]
            shadow_trades = [trade for trade in trades if trade.get("is_shadow")]

            real_wins = sum(1 for trade in real_trades if trade["pnl_percent"] > 0)
            real_wr = (real_wins / len(real_trades)) * 100 if real_trades else 0
            real_pnl = sum(trade["pnl_percent"] for trade in real_trades)

            shadow_wins = sum(1 for trade in shadow_trades if trade["pnl_percent"] > 0)
            shadow_wr = (shadow_wins / len(shadow_trades)) * 100 if shadow_trades else 0
            shadow_pnl = sum(trade["pnl_percent"] for trade in shadow_trades)

            top_symbols = Counter(trade["symbol"] for trade in trades).most_common(5)

            msg = (
                f"📊 *REPORTE DE AUDITORÍA (Últimos {len(trades)} Trades)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 *General:*\n"
                f"  - Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
                f"  - PnL Total: {total_pnl:+.2f}%\n"
                f"  - PnL Promedio: {avg_pnl:+.2f}%\n\n"
                f"🔥 *Reales ({len(real_trades)}):*\n"
                f"  - Win Rate: {real_wr:.1f}%\n"
                f"  - PnL Total: {real_pnl:+.2f}%\n\n"
                f"👻 *Shadows ({len(shadow_trades)}):*\n"
                f"  - Win Rate: {shadow_wr:.1f}%\n"
                f"  - PnL Total: {shadow_pnl:+.2f}%\n\n"
                f"🏆 *Símbolos más operados:*\n"
            )
            for symbol, count in top_symbols:
                msg += f"  - {symbol}: {count} trades\n"

            send_telegram_msg(msg)
        except Exception as error:
            send_telegram_msg(f"❌ Error generando auditoría: {error}")
        return True

    if text == "/status":
        ai = bot.brain.get_ai_maturity()
        pnl_pct, pnl_usd = bot.brain.get_daily_real_pnl(bot.balance)

        exigencia_txt = "Normal"
        if bot.dynamic_offset > 0:
            exigencia_txt = f"🔒 ALTA (+{bot.dynamic_offset * 100:.0f}% req)"

        msg = (
            f"📊 *ESTADO {Config.VERSION}*\n"
            f"• Modo: {'🧪 PAPER/SHADOW' if Config.PAPER_MODE else '🔥 REAL'}\n"
            f"• Motor TF: 1H | Macro: 4H\n"
            f"• IA: {ai['rank']} ({ai['xp_percent']}%)\n"
            f"• PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
            f"• Exigencia: {exigencia_txt}\n"
            f"• Python: {platform.python_version()}"
        )
        send_telegram_msg(msg)
        return True

    if text == "/signals":
        if hasattr(bot, "last_signal_stats"):
            stats = bot.last_signal_stats
            total = stats["BUY"] + stats["SELL"] + stats["NEUTRAL"]
            if total > 0:
                buy_pct = (stats["BUY"] / total) * 100
                sell_pct = (stats["SELL"] / total) * 100
                neutral_pct = (stats["NEUTRAL"] / total) * 100
                msg = (
                    f"📊 *DISTRIBUCIÓN DE SEÑALES*\n\n"
                    f"*Señales Técnicas:*\n"
                    f"• BUY: {stats['BUY']} ({buy_pct:.1f}%)\n"
                    f"• SELL: {stats['SELL']} ({sell_pct:.1f}%)\n"
                    f"• NEUTRAL: {stats['NEUTRAL']} ({neutral_pct:.1f}%)\n\n"
                    f"*Veredictos:*\n"
                    f"• ✅ REAL: {stats['REAL']}\n"
                    f"• 🧪 SHADOW: {stats['SHADOW']}\n"
                    f"• ❌ VETO: {stats['VETO']}\n\n"
                    f"*Watchlist Breakout:*\n"
                    f"• Total: {bot.breakout_agent.size()}\n"
                    f"• SHOCK_VETO: {bot.breakout_agent.summary_by_source().get('SHOCK_VETO', 0)}\n"
                    f"• COHERENCE_VETO: {bot.breakout_agent.summary_by_source().get('COHERENCE_VETO', 0)}\n\n"
                    f"Total pares escaneados: {total}"
                )
            else:
                msg = "⚠️ No hay datos del último ciclo de escaneo."
        else:
            msg = "⚠️ Aún no se ha completado un ciclo de escaneo."
        send_telegram_msg(msg)
        return True

    if text == "/shadow_stats":
        try:
            conn = bot.brain._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 1")
            total_shadow = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 1 AND pnl_percent > 0")
            wins = cursor.fetchone()[0]
            cursor.execute(
                "SELECT AVG(pnl_percent) FROM trades WHERE is_shadow = 1 AND pnl_percent != -99.0"
            )
            avg_pnl = cursor.fetchone()[0] or 0
            conn.close()
        except Exception as error:
            send_telegram_msg(f"❌ Error obteniendo stats shadow: {error}")
            total_shadow, wins, avg_pnl = 0, 0, 0.0

        wr = (wins / total_shadow * 100) if total_shadow > 0 else 0
        msg = (
            f"🧪 *ESTADÍSTICAS SHADOW*\n\n"
            f"• Total Trades: {total_shadow}\n"
            f"• Win Rate: {wr:.1f}%\n"
            f"• PnL Promedio: {avg_pnl:.2f}%\n\n"
            f"_Los shadow trades son para aprendizaje y no arriesgan capital real._"
        )
        send_telegram_msg(msg)
        return True

    if text == "/audit_db":
        send_telegram_msg("🔍 *ANALIZANDO UTILIDAD DE DATOS EN DB...*")
        try:
            import sqlite3

            from tools.data_utility_audit import analysis_summary

            db_path = getattr(
                bot.brain, "_db_path", "/home/miguel/Pbot-V5ARCH-DEV-main/sniper_brain.db"
            )
            conn = sqlite3.connect(str(db_path))
            report = analysis_summary(conn, Path(db_path))
            conn.close()

            db_info = report.get("database", {})
            features = report.get("signal_alerts_features", {})
            recs = report.get("recommendations", [])

            warnings = sum(1 for r in recs if r["priority"] == "HIGH")
            medium = sum(1 for r in recs if r["priority"] == "MEDIUM")

            samples = features.get("samples", 0)
            redundant = len(features.get("redundant_groups", []))
            dead_fields = sum(1 for f in features.get("fields", []) if f["utility"] == "DEAD")

            msg = (
                f"📊 *AUDITORÍA DE DB*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• Tamaño: {db_info.get('size_human', '?')}\n"
                f"• Señales en features_json: {samples}\n"
                f"• Campos redundantes: {redundant}\n"
                f"• Campos muertos: {dead_fields}\n"
                f"• Alertas: 🔴{warnings} 🟡{medium}\n\n"
            )
            if recs:
                msg += "⚠️ *Recomendaciones:*\n"
                for r in recs[:5]:
                    icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪", "INFO": "ℹ️"}
                    msg += f"{icon.get(r['priority'], '•')} {r['message'][:120]}\n"
                if len(recs) > 5:
                    msg += f"... y {len(recs) - 5} más (ver audit_report.json)\n"
            send_telegram_msg(msg)
        except Exception as error:
            send_telegram_msg(f"❌ Error auditando DB: {error}")
        return True

    return False

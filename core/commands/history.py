import json
import sqlite3
from collections import Counter
from datetime import datetime

from tools.notifier import send_telegram_msg


def _handle_history_commands(bot, text: str) -> bool:
    if text == "/paper_review":
        trades = bot.brain.get_paper_trades_history(limit=50)
        if not trades:
            send_telegram_msg("📭 No hay historial de trades PAPER/REAL para analizar.")
            return True

        wins = sum(1 for trade in trades if trade["pnl_percent"] > 0)
        total = len(trades)
        wr = (wins / total) * 100
        total_pnl = sum(trade["pnl_percent"] for trade in trades)
        avg_pnl = total_pnl / total

        msg = (
            f"📝 *ANÁLISIS DE PAPER TRADES (Últimos {total})*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Win Rate:* {wr:.1f}%\n"
            f"📈 *PnL Acumulado:* {total_pnl:+.2f}%\n"
            f"📊 *Promedio:* {avg_pnl:+.2f}% por trade\n\n"
            f"💡 _Si esto fuera dinero real, tendrías un PnL de {total_pnl:+.2f}%_"
        )
        send_telegram_msg(msg)
        return True

    if text == "/performance_trends":
        trends = bot.brain.get_stats_by_trend()
        if not trends:
            send_telegram_msg("📭 Aún no hay suficientes datos con snapshots para este análisis.")
            return True

        msg = "📊 *EFICIENCIA POR TIPO DE MERCADO*\n━━━━━━━━━━━━━━━━━━━━\n"
        icons = {
            "UP": "🚀 ALCISTA",
            "DOWN": "📉 BAJISTA",
            "RANGO": "↔️ RANGO",
            "NEUTRAL": "⚪ NEUTRAL",
        }
        for label, data in trends.items():
            icon = icons.get(label, f"❓ {label}")
            msg += (
                f"{icon}:\n"
                f"• Trades: {data['total']}\n"
                f"• Winrate: *{data['winrate']}%*\n"
                f"• PnL Promedio: {data['avg_pnl']:+.2f}%\n\n"
            )
        msg += "💡 _Dato: La IA usa estos números para autogestionar su riesgo._"
        send_telegram_msg(msg)
        return True

    if text == "/shadow_report":
        trades = bot.brain.get_todays_trades()
        shadows = [trade for trade in trades if trade.get("is_shadow")]

        c_today = len(shadows)
        wins_list = [trade["pnl_percent"] for trade in shadows if trade["pnl_percent"] > 0]
        losses_list = [trade["pnl_percent"] for trade in shadows if trade["pnl_percent"] <= 0]

        w_today = len(wins_list)
        l_today = len(losses_list)
        wr_today = (w_today / c_today * 100) if c_today > 0 else 0.0

        avg_win_pct = sum(wins_list) / w_today if w_today > 0 else 0.0
        avg_loss_pct = sum(losses_list) / l_today if l_today > 0 else 0.0

        base_usd = 20.0
        avg_win_usd = (avg_win_pct / 100) * base_usd
        avg_loss_usd = (avg_loss_pct / 100) * base_usd

        c_total, w_total, l_total, wr_total = 0, 0, 0, 0.0
        h_avg_win, h_avg_loss = 0.0, 0.0
        try:
            db_path = getattr(bot.brain, "db_name", "sniper_brain.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*), SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END), AVG(CASE WHEN pnl_percent > 0 THEN pnl_percent END), AVG(CASE WHEN pnl_percent <= 0 THEN pnl_percent END) FROM trades WHERE is_shadow=1"
            )
            row = cursor.fetchone()
            if row:
                c_total = row[0] or 0
                w_total = row[1] or 0
                h_avg_win = row[2] or 0.0
                h_avg_loss = row[3] or 0.0
                l_total = c_total - w_total
                wr_total = (w_total / c_total * 100) if c_total > 0 else 0.0
            conn.close()
        except Exception as error:
            bot.log(f"⚠️ Error DB Shadow Report: {error}")

        h_win_usd = (h_avg_win / 100) * base_usd
        h_loss_usd = (h_avg_loss / 100) * base_usd

        msg = (
            f"👻 *REPORTE SHADOW (Modo Aspiradora)*\n"
            f"📅 {datetime.now().strftime('%d/%m/%Y')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 *HOY:*\n"
            f"• Trades: {c_today}\n"
            f"• Ganados: {w_today} ✅\n"
            f"• Perdidos: {l_today} ❌\n"
            f"• Win Rate: *{wr_today:.1f}%*\n"
            f"• Avg Win: +{avg_win_pct:.1f}% (+${avg_win_usd:.2f})\n"
            f"• Avg Loss: {avg_loss_pct:.1f}% (${avg_loss_usd:.2f})\n\n"
            f"📚 *HISTÓRICO TOTAL:*\n"
            f"• Trades: {c_total}\n"
            f"• Ganados: {w_total} ✅\n"
            f"• Perdidos: {l_total} ❌\n"
            f"• Win Rate: *{wr_total:.1f}%*\n"
            f"• Avg Win: +{h_avg_win:.1f}% (+${h_win_usd:.2f})\n"
            f"• Avg Loss: {h_avg_loss:.1f}% (${h_loss_usd:.2f})\n"
        )

        if c_today > 0:
            counts = Counter([trade["symbol"] for trade in shadows])
            msg += "\n🏆 *Top Activos Explorados (Hoy):*\n"
            for symbol, count in counts.most_common(5):
                sym_trades = [trade for trade in shadows if trade["symbol"] == symbol]
                sym_wins = sum(1 for trade in sym_trades if trade["pnl_percent"] > 0)
                sym_wr = sym_wins / len(sym_trades) * 100
                msg += f"• {symbol}: {count} trades ({sym_wr:.0f}% WR)\n"
        else:
            msg += "\n⚠️ No se han registrado operaciones Shadow hoy."

        send_telegram_msg(msg)
        return True

    if text.startswith("/dna"):
        parts = text.split()
        symbol = parts[1].upper() if len(parts) > 1 else "BTC/USDT"
        genes = bot.brain.get_genetic_params(symbol)
        if genes:
            msg = (
                f"🧬 *DNA STATUS: {symbol}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• SL Multiplier: {genes['sl_mult']:.2f}\n"
                f"• TP Multiplier: {genes['tp_mult']:.2f}\n"
                f"• Generation: {genes.get('generation', 1)}"
            )
        else:
            msg = f"⚠️ Sin datos genéticos para {symbol}"
        send_telegram_msg(msg)
        return True

    if text.startswith("/trade_detail"):
        parts = text.split()
        symbol = parts[1].upper() if len(parts) > 1 else None

        if not symbol:
            send_telegram_msg("⚠️ Uso: /trade_detail [SÍMBOLO]\nEj: /trade_detail BTC/USDT")
            return True

        found = None
        for item in bot.scanner_history:
            if symbol in item.get("symbol", ""):
                found = item
                break

        if not found:
            send_telegram_msg(f"⚠️ No hay datos recientes para {symbol}")
            return True

        rsi = found.get("rsi_val", 0)
        adx = found.get("adx_val", 0)
        z_score = found.get("z_score", 0.0)
        ia_prob = found.get("ia_prob", "0%")
        signal = found.get("signal", "WAIT")
        result = found.get("result", "N/A")
        ob = found.get("ob", "⚪")
        trend = found.get("trend_val", "N/A")
        funding = found.get("funding_rate", 0.0)
        votos = found.get("votos", {})

        msg = (
            f"🔍 *ANÁLISIS DETALLADO: {found['symbol']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *SEÑAL:* {signal} | Prob: *{ia_prob}*\n"
            f"📈 *RESULTADO:* {result}\n\n"
            f"🛠️ *INDICADORES TÉCNICOS:*\n"
            f"• RSI: {rsi} | ADX: {adx}\n"
            f"• Z-Score: {z_score:.2f}\n"
            f"• Trend: {trend}\n"
            f"• Funding: {funding * 100:.3f}%\n"
            f"• OB Status: {ob}\n\n"
        )

        if votos:
            msg += "🗳️ *VOTOS DE AGENTES:*\n"
            agent_names = {"MT": "📈 Tend", "SR": "🧱 Estr", "G": "👻 IA"}
            for agent_id, vote in sorted(votos.items(), key=lambda x: x[1], reverse=True):
                name = agent_names.get(agent_id, agent_id)
                bar = "█" * int(vote / 10) + "░" * (10 - int(vote / 10))
                msg += f"{name}: {bar} {vote:.0f}%\n"

        msg += "\n💡 _Comando: /thinking para ver vetos recientes_"
        send_telegram_msg(msg)
        return True

    if text.startswith("/trade "):
        parts = text.split()
        try:
            trade_id = int(parts[1])
        except (ValueError, IndexError):
            send_telegram_msg("⚠️ Uso: /trade [ID]\nEj: /trade 10258")
            return True

        trade = bot.brain.get_trade_by_id(trade_id)
        if not trade:
            send_telegram_msg(f"❌ No se encontró el trade #{trade_id}")
            return True

        symbol = trade.get("symbol", "N/A")
        side = trade.get("side", "N/A")
        entry = trade.get("entry_price", 0)
        exit_p = trade.get("exit_price", 0)
        pnl = trade.get("pnl", 0)
        pnl_pct = trade.get("pnl_percent", 0)
        reason = trade.get("reason", "N/A")
        timestamp = trade.get("timestamp", "N/A")
        is_shadow = trade.get("is_shadow", 0)
        fees = trade.get("fees", 0)
        rsi = trade.get("rsi", 0)
        adx = trade.get("adx", 0)
        funding = trade.get("funding_rate", 0)
        vol_rel = trade.get("vol_rel", 0)
        entry_ob = trade.get("entry_ob", "⚪")

        mode = "🧪 SHADOW" if is_shadow else "🔥 REAL"

        msg = (
            f"📋 *TRADE #{trade_id}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 Símbolo: {symbol}\n"
            f"🔹 Lado: {side} | Modo: {mode}\n"
            f"🔹 Entrada: {entry:.4f} | Salida: {exit_p:.4f}\n"
            f"🔹 PnL: {pnl:+.4f} USD ({pnl_pct:+.2f}%)\n"
            f"🔹 Fees: {fees:.4f} USD\n"
            f"🔹 Razón: {reason}\n"
            f"🔹 Hora: {timestamp}\n\n"
        )

        msg += (
            f"📊 *CONTEXTO DEL MERCADO:*\n"
            f"• RSI: {rsi:.1f} | ADX: {adx:.1f}\n"
            f"• Funding: {funding * 100:.3f}%\n"
            f"• Vol Rel: {vol_rel:.2f}\n"
            f"• Entry OB: {entry_ob}\n\n"
        )

        market_snap = trade.get("market_snapshot")
        if market_snap:
            try:
                snap = json.loads(market_snap)
                trend = snap.get("trend", "N/A")
                z_score = snap.get("z_score", 0)
                bb_pos = snap.get("bb_pos", 0.5)
                dist_ema = snap.get("dist_ema", 0)
                btc_delta = snap.get("btc_delta_tf", 0)

                msg += (
                    f"🧠 *ANÁLISIS IA:*\n"
                    f"• Tendencia: {trend}\n"
                    f"• Z-Score: {z_score:.2f}\n"
                    f"• BB Position: {bb_pos:.2f}\n"
                    f"• Dist EMA: {dist_ema:.2f}\n"
                    f"• BTC Delta: {btc_delta:.2f}%\n\n"
                )
            except Exception as error:
                bot.log(f"⚠️ No se pudo parsear market_snapshot en trade {trade_id}: {error}")

        similar = bot.brain.get_similar_trades(rsi, adx, limit=3)
        if similar:
            msg += "🔗 *TRADES SIMILARES (RAG):*\n"
            for similar_trade in similar:
                sim_pnl = similar_trade.get("pnl_percent", 0)
                sim_sym = similar_trade.get("symbol", "N/A")
                sim_id = similar_trade.get("id", 0)
                msg += f"• #{sim_id} {sim_sym}: {sim_pnl:+.2f}%\n"

        send_telegram_msg(msg)
        return True

    return False

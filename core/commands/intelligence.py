import importlib.util

from config import Config
from core.telegram_api import sanitize_telegram_error, telegram_post
from tools.notifier import send_telegram_msg


def _handle_intelligence_commands(bot, text: str) -> bool:
    if text == "/thinking":
        vetos = bot.brain.get_recent_vetos(limit=3)
        msg = "🧠 *PROCESO DE PENSAMIENTO IA*\n━━━━━━━━━━━━━━━━━━━━\n"
        if not vetos:
            msg += "Esperando nuevas señales para analizar..."
        else:
            for veto in vetos:
                msg += f"📍 *{veto['symbol']}*: {veto['reason']}\n"
                msg += f"💡 _Contexto:_ {veto['context_summary']}\n\n"
        msg += f"🔄 *Estado:* Analizando {len(bot.pairs_to_scan)} pares."
        send_telegram_msg(msg)
        return True

    if text == "/watchlist":
        rows = list(bot.breakout_agent.watchlist.values())
        if not rows:
            send_telegram_msg("👁️ *WATCHLIST BREAKOUT*\n\n(vacía)")
            return True

        rows = sorted(
            rows,
            key=lambda row: (
                float(row.get("ia_prob", 0.0) or 0.0),
                float(row.get("updated_at", 0.0) or 0.0),
            ),
            reverse=True,
        )
        source_counts = bot.breakout_agent.summary_by_source()
        source_txt = " | ".join(
            [f"{key}:{int(value)}" for key, value in sorted(source_counts.items())]
        )
        if not source_txt:
            source_txt = "N/A"

        msg = f"👁️ *WATCHLIST BREAKOUT*\n• Total: {len(rows)}\n• Fuentes: {source_txt}\n\n"
        for row in rows[:10]:
            meta = row.get("meta") or {}
            source = str(meta.get("source", "UNK"))
            dist = meta.get("shock_dist_pct")
            dist_txt = f"{float(dist):.2f}%" if isinstance(dist, (int, float)) else "--"
            msg += (
                f"• {row.get('symbol')} {row.get('side')} | IA {float(row.get('ia_prob', 0.0)):.1f}%"
                f" | dist {dist_txt} | src {source}\n"
            )

        if len(rows) > 10:
            msg += f"\n... +{len(rows) - 10} más"
        send_telegram_msg(msg)
        return True

    if text == "/quarantine":
        msg = "☣️ *ZONA DE CUARENTENA (Strike System)*\n"
        msg += "Monedas bloqueadas por 3 pérdidas consecutivas:\n━━━━━━━━━━━━━━━━━━━━\n"
        count = 0
        for symbol_raw in bot.pairs_to_scan:
            symbol = symbol_raw.split(":")[0]
            if bot.brain.check_consecutive_losses(symbol, 15):
                msg += f"• {symbol} 🚫\n"
                count += 1
        if count == 0:
            msg += "✅ Ninguna moneda en cuarentena. El mercado está sano."
        else:
            msg += f"\nTotal: {count} activos vetados temporalmente."
        send_telegram_msg(msg)
        return True

    if text == "/agents":
        reps = bot.brain.get_agent_reputation()
        msg = "🕵️ *REPUTACIÓN DE AGENTES (CONFIDENCE)*\n━━━━━━━━━━━━━━━━━━━━\n"
        agent_names = {
            "MT": "📈 Tendencia (MT)",
            "SR": "🧱 Estructura (SR)",
            "G": "👻 IA (G)",
            "R": "🧠 RAG Vectorial",
        }
        for agent_id, score in sorted(reps.items(), key=lambda item: item[1], reverse=True):
            name = agent_names.get(agent_id, agent_id)
            icon = "🟢" if score >= 100 else ("🟡" if score >= 90 else "🔴")
            msg += f"{icon} *{name}:* {score:.1f}\n"
        msg += "\n_Nota: >100 = Racha Ganadora | <90 = En Observación_"
        send_telegram_msg(msg)
        return True

    if text == "/intelligence":
        intel = bot.brain.get_model_insights()
        ai_xp = bot.brain.get_ai_maturity()
        msg = (
            f"🧠 *MAPA MENTAL DE LA IA (v106.0)*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Confianza:* {ai_xp['rank']} ({ai_xp['xp_percent']}%)\n\n"
            f"🔍 *INDICADORES MÁS RELEVANTES:*\n"
        )
        for feature, importance in intel["top_features"]:
            msg += f"• {feature.upper()}: {importance * 100:.1f}% de peso\n"
        msg += f"\n🎯 *ESTRATEGIA RECIÉN APRENDIDA:*\n_{intel['learned_rule']}_"
        send_telegram_msg(msg)
        return True

    if text == "/ai_intel":
        send_telegram_msg("🎨 Generando inteligencia visual (XAI)...")
        try:
            if importlib.util.find_spec("tools.ai_mapper") is None:
                send_telegram_msg("ℹ️ Módulo visual no disponible (`tools.ai_mapper`).")
                return True

            from tools.ai_mapper import generate_ai_intel_image

            image_bio = generate_ai_intel_image(bot.brain)
            files = {"photo": ("ai_intel.png", image_bio, "image/png")}
            telegram_post(
                "sendPhoto",
                data={"chat_id": Config.TELEGRAM_CHAT_ID},
                files=files,
                timeout=(5, 20),
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error generando XAI: {sanitize_telegram_error(error)}")
        return True

    if text == "/report":
        from tools.reporter import generate_mobile_report

        msg = generate_mobile_report(bot.balance)
        send_telegram_msg(f"📝 *REPORTE DE RENDIMIENTO*\n{msg}")
        return True

    if text == "/open":
        with bot.lock:
            if not bot.active_trades:
                send_telegram_msg("📭 No hay trades activos en este momento.")
                return True
            trades_list = list(bot.active_trades.items())

        if len(trades_list) > 20:
            msg = f"🔍 *POSICIONES ABIERTAS ({len(trades_list)})* - Top 10\n"
            for symbol, trade in trades_list[:10]:
                msg += f"\n• {symbol} ({trade['side']}): {trade.get('pnl', 0):+.2f}%"
            msg += "\n\n⚠️ _Lista truncada. Revise logs para detalle completo._"
            send_telegram_msg(msg)
        else:
            msg = "🔍 *POSICIONES ABIERTAS*\n"
            for symbol, trade in trades_list:
                msg += f"\n• {symbol} ({trade['side']}): {trade.get('pnl', 0):+.2f}%"
            send_telegram_msg(msg)
        return True

    if text == "/top":
        tops = [
            item
            for item in bot.scanner_history
            if float(item.get("ia_prob", "0%").replace("%", "")) > 90
        ]
        if not tops:
            send_telegram_msg("🔭 No hay señales de alta probabilidad.")
        else:
            msg = "🎯 *TOP 3 SEÑALES IA*\n"
            for item in sorted(
                tops,
                key=lambda x: float(x.get("ia_prob", "0%").replace("%", "")),
                reverse=True,
            )[:3]:
                msg += f"\n💎 {item['symbol']}: *{item['ia_prob']}*"
            send_telegram_msg(msg)
        return True

    if text == "/targets":
        if not bot.pairs_to_scan:
            send_telegram_msg("🔭 Radar vacío o inicializando...")
        else:
            msg = f"🎯 *OBJETIVOS ACTIVOS ({len(bot.pairs_to_scan)})*\n"
            pairs_str = ", ".join(bot.pairs_to_scan)
            if len(pairs_str) > 4000:
                pairs_str = pairs_str[:4000] + "..."
            send_telegram_msg(f"{msg}{pairs_str}")
        return True

    return False

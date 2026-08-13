import asyncio
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from config import Config
from core.config.portable_paths import get_log_path
from core.cooldown_state import persist_cooldowns
from core.model_loader import ROOT, resolve_script_path
from core.symbol_utils import normalize_position_symbol
from core.time_utils import monotonic_now
from tools.notifier import send_telegram_msg


def _help_message() -> str:
    return (
        "🤖 *SNIPER AI v118 - CENTRO DE MANDO*\n\n"
        "🕒 *MARCO OPERATIVO*\n"
        "• Motor principal: *1H*\n"
        "• Filtro macro: *4H (veto direccional)*\n"
        "• Modo actual: *PAPER/SHADOW*\n"
        "• Ejecución: solo setup institucional (sin 5m/15m)\n\n"
        "🕹️ *CONTROL*\n"
        "• `/on` | `/resume`: Activar sistema\n"
        "• `/off` | `/pause`: Pausar sistema\n"
        "• `/panic`: Cierre de emergencia\n"
        "• `/recover_halt`: Liberar HALT solo con snapshots exchange planos\n"
        "• `/unquarantine`: Resetear cooldown de pares\n\n"
        "• `/force_clear [PAR]`: Liberar recovery bloqueado con verificación\n\n"
        "📊 *AUDITORÍA*\n"
        "• `/status`: Estado operativo actual\n"
        "• `/audit_report`: Auditoría últimos 100 trades\n"
        "• `/open`: Ver operaciones abiertas\n"
        "• `/targets`: Ver radar de objetivos\n"
        "• `/signals`: Distribución de señales\n"
        "• `/pipeline`: Estado HMM/WS/pipeline\n"
        "• `/shadow_stats`: Estadísticas modo Shadow\n"
        "• `/sre_intent`: SLA intents 1h/24h\n"
        "• `/tiers`: Señales por Tier\n"
        "• `/top`: Top señales por probabilidad\n"
        "• `/thresholds`: Umbrales actuales del motor 1H\n\n"
        "🔍 *ANÁLISIS*\n"
        "• `/trade_detail [PAR]`: Análisis profundo de un par\n"
        "• `/trade [ID]`: Detalle de trade histórico\n"
        "• `/thinking`: Vetos recientes de la IA\n"
        "• `/watchlist`: Estado de acecho breakout\n"
        "• `/intelligence`: Mapa mental del modelo\n"
        "• `/agents`: Reputación de agentes\n"
        "• `/explain [PAR]`: Explicación en tiempo real\n\n"
        "🧠 *INTELIGENCIA*\n"
        "• `/force_train`: Re-entrenar modelo Ghost\n"
        "• `/dna [PAR]`: Parámetros genéticos\n\n"
        "⚙️ *SISTEMA*\n"
        "• `/reset`: Reiniciar PnL diario\n"
        "• `/api_status` | `/weight`: Estado del API Weight (Binance)\n"
        "• `/test`: Test de notificaciones\n\n"
        "🚫 *COMANDOS BLOQUEADOS EN CUARENTENA*\n"
        "• `/force_shadow`, `/clean`, `/dump_db`, `/evolution` y `/genetic`"
    )


def _handle_misc_commands(bot, text: str) -> bool:
    if text == "/pipeline":
        try:
            ws_ts = float(getattr(bot, "market_btc_price_ts", 0.0) or 0.0)
            ws_age = monotonic_now() - ws_ts if ws_ts > 0 else None
            ws_age_text = f"{ws_age:.1f}s" if ws_age is not None else "n/a"
            regime_conf = getattr(bot, "market_regime_confidence", None)
            regime_conf_text = (
                f"{float(regime_conf) * 100:.1f}%" if regime_conf is not None else "n/a"
            )
            hmm_snapshot = getattr(bot, "hmm_markov_snapshot", {}) or {}
            markov_state = hmm_snapshot.get("state", "UNKNOWN")
            bullish_prob = float(hmm_snapshot.get("bullish_breakout_prob", 0.0) or 0.0)
            bearish_prob = float(hmm_snapshot.get("bearish_reversal_prob", 0.0) or 0.0)
            range_prob = float(hmm_snapshot.get("range_prob", 0.0) or 0.0)
            markov_ts = hmm_snapshot.get("ts")
            markov_age_text = "n/a"
            if markov_ts:
                parsed_ts = datetime.fromisoformat(str(markov_ts).replace("Z", "+00:00"))
                if parsed_ts.tzinfo is None:
                    parsed_ts = parsed_ts.replace(tzinfo=UTC)
                markov_age = max(0.0, (datetime.now(UTC) - parsed_ts).total_seconds())
                markov_age_text = f"{markov_age / 60:.1f}m"
            markov_stats = getattr(bot, "markov_decision_stats", {}) or {}
            send_telegram_msg(
                "🧬 *PIPELINE STATUS*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Régimen: {getattr(bot, 'market_regime', 'UNKNOWN')}\n"
                f"🔎 Fuente régimen: {getattr(bot, 'market_regime_source', 'UNKNOWN')}\n"
                f"🎯 Confianza HMM: {regime_conf_text}\n"
                f"🧮 Markov: {markov_state} | age {markov_age_text}\n"
                f"📈 Bull {bullish_prob:.1f}% | 📉 Bear {bearish_prob:.1f}% | ↔️ Range {range_prob:.1f}%\n"
                f"🟢 Range breakout: {int(markov_stats.get('range_breakout_allowed', 0) or 0)} | "
                f"🟡 Penaliza: {int(markov_stats.get('range_standard_penalty', 0) or 0)} | "
                f"🔴 Stagnant: {int(markov_stats.get('range_stagnant_veto', 0) or 0)}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"₿ BTC: ${float(getattr(bot, 'market_btc_price', 0.0) or 0.0):,.2f}\n"
                f"📡 Fuente BTC: {getattr(bot, 'market_btc_price_source', 'UNKNOWN')}\n"
                f"⏱️ Edad WS BTC: {ws_age_text}\n"
                f"🧠 HMM Range Veto: {bool(getattr(Config, 'HMM_RANGE_VETO', False))}\n"
                f"🧪 Paper Mode: {bool(getattr(Config, 'PAPER_MODE', True))}"
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error /pipeline: {error}")
        return True

    if text == "/sre_intent":
        try:
            events_path = Path(get_log_path("execution_events.jsonl"))
            if not events_path.exists():
                send_telegram_msg("ℹ️ SRE Intent: aún no existe logs/execution_events.jsonl")
                return True

            now_utc = datetime.now(UTC)
            cut_1h = now_utc - timedelta(hours=1)
            cut_24h = now_utc - timedelta(hours=24)

            ack_1h = exp_1h = 0
            ack_24h = exp_24h = 0

            with events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue

                    event = str(row.get("event") or "")
                    if event not in {"ENTRY_ORDER_ACK", "INTENT_EXPIRED"}:
                        continue

                    raw_ts = row.get("ts")
                    if not raw_ts:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(raw_ts))
                    except Exception:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    else:
                        ts = ts.astimezone(UTC)

                    if ts >= cut_24h:
                        if event == "ENTRY_ORDER_ACK":
                            ack_24h += 1
                        else:
                            exp_24h += 1
                    if ts >= cut_1h:
                        if event == "ENTRY_ORDER_ACK":
                            ack_1h += 1
                        else:
                            exp_1h += 1

            ratio_1h = (exp_1h / ack_1h * 100.0) if ack_1h > 0 else 0.0
            ratio_24h = (exp_24h / ack_24h * 100.0) if ack_24h > 0 else 0.0

            def _level(ratio: float) -> str:
                if ratio >= 1.0:
                    return "🚨 CRITICAL"
                if ratio >= 0.5:
                    return "⚠️ WARNING"
                return "✅ OK"

            api_weight_txt = "n/a"
            if getattr(bot, "weight_tracker", None):
                try:
                    st = bot.weight_tracker.get_status()
                    api_weight_txt = (
                        f"{st.get('current_weight', 0)}/{st.get('limit', 2400)} "
                        f"({st.get('usage_pct', 0.0):.1f}%)"
                    )
                except Exception:
                    api_weight_txt = "error"

            send_telegram_msg(
                "🛡️ *SRE INTENT SLA*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"1h  • ACK={ack_1h} EXP={exp_1h} RATIO={ratio_1h:.2f}% {_level(ratio_1h)}\n"
                f"24h • ACK={ack_24h} EXP={exp_24h} RATIO={ratio_24h:.2f}% {_level(ratio_24h)}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⚖️ API Weight (1m): {api_weight_txt}\n"
                "SLO: warning>=0.5% | critical>=1.0%"
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error /sre_intent: {error}")
        return True

    if text == "/tiers":
        slock = getattr(bot, "scanner_lock", None)
        if slock:
            with slock:
                has_history = bool(bot.scanner_history)
        else:
            has_history = bool(bot.scanner_history)
        if not has_history:
            send_telegram_msg("🕵️ *TIERS:* No hay señales en el radar todavía.")
            return True

        tiers: dict[str, list[str]] = {"ELITE": [], "GOLD": [], "SILVER": [], "IRON": []}
        hist = []
        if slock:
            with slock:
                hist = list(bot.scanner_history) if bot.scanner_history else []
        else:
            hist = list(bot.scanner_history) if bot.scanner_history else []
        for item in hist:
            tier = item.get("tier", "IRON")
            if tier in tiers:
                tiers[tier].append(f"{item['symbol']} ({item['ia_prob']})")

        msg = "🏆 *SEÑALES POR TIER*\n\n"
        if tiers["ELITE"]:
            msg += "💎 *ELITE*\n" + "\n".join([f"• {x}" for x in tiers["ELITE"][:10]]) + "\n\n"
        if tiers["GOLD"]:
            msg += "🥇 *GOLD*\n" + "\n".join([f"• {x}" for x in tiers["GOLD"][:10]]) + "\n\n"
        if tiers["SILVER"]:
            msg += "🥈 *SILVER*\n" + "\n".join([f"• {x}" for x in tiers["SILVER"][:10]]) + "\n"

        if not tiers["ELITE"] and not tiers["GOLD"] and not tiers["SILVER"]:
            msg += "⚪ Solo señales IRON detectadas."

        send_telegram_msg(msg)
        return True

    if text == "/dump_db":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nLa exportación remota de DB fue retirada porque dependía de un script ausente."
        )
        return True

    return False


def _handle_training_and_maintenance_commands(bot, text: str) -> bool:
    if text in ["/train", "/force_train"]:
        loop = getattr(bot, "main_loop", None)
        if loop is None or not loop.is_running():
            send_telegram_msg(
                "❌ Entrenamiento bloqueado: Global Event Loop inalcanzable. Reinicia el bot para restaurar el runtime."
            )
            return True

        dispatch_lock = getattr(bot, "_training_dispatch_lock", None)
        if dispatch_lock is None:
            dispatch_lock = threading.Lock()
            setattr(bot, "_training_dispatch_lock", dispatch_lock)

        with dispatch_lock:
            in_flight = getattr(bot, "_training_future", None)
            if in_flight is not None and not in_flight.done():
                send_telegram_msg(
                    "⏳ Ya hay un entrenamiento en curso. Espera a que termine para lanzar otro."
                )
                return True

        send_telegram_msg("🧠 *FORZANDO ENTRENAMIENTO...* (Background Process)")

        async def run_training():
            try:
                ghost_trainer = resolve_script_path(ROOT / "tools" / "ghost_trainer.py")
                process = await asyncio.create_subprocess_exec(
                    "nice",
                    "-n",
                    "15",
                    sys.executable,
                    str(ghost_trainer),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # 2. Espera no bloqueante del Event Loop (máx 10 min)
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)

                if process.returncode == 0:
                    # 3. Notificación de Disponibilidad (No recarga inmediata)
                    bot.brain.pending_model_update = True
                    send_telegram_msg(
                        "✅ *Entrenamiento completo.* El nuevo modelo está listo.\n"
                        "Esperando ventana segura (0 trades activos) para recarga automática."
                    )
                else:
                    error_msg = stderr.decode().strip()
                    send_telegram_msg(
                        f"❌ Fallo en entrenamiento (Cod {process.returncode}).\n"
                        f"El modelo actual sigue intacto.\nError: {error_msg[-200:]}"
                    )
            except Exception as e:
                send_telegram_msg(f"❌ Error crítico en subproceso: {e}")

        def _on_training_done(_future):
            with dispatch_lock:
                setattr(bot, "_training_future", None)

        try:
            future = asyncio.run_coroutine_threadsafe(run_training(), loop)
            with dispatch_lock:
                setattr(bot, "_training_future", future)
            future.add_done_callback(_on_training_done)
            send_telegram_msg("⚙️ Solicitud de entrenamiento enviada al Loop Principal.")
        except Exception as e:
            with dispatch_lock:
                setattr(bot, "_training_future", None)
            send_telegram_msg(f"❌ Error al delegar entrenamiento: {e}")

        return True

    if text == "/evolution":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nAI Coach no está instalado en este entorno y se bloquea para evitar falsas ejecuciones."
        )
        return True

    if text == "/genetic":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nEl motor genético remoto fue retirado porque el script no existe en este despliegue."
        )
        return True

    if text == "/force_shadow":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nEl bot opera en cuarentena controlada y no permite alternar modo por Telegram."
        )
        return True

    if text.startswith("/explain"):
        parts = text.split()
        if len(parts) < 2:
            send_telegram_msg("⚠️ Uso: /explain [SYMBOL] (ej: /explain BTC/USDT)")
            return True

        sym = parts[1].upper()
        send_telegram_msg(f"🧠 *ANALIZANDO {sym}...*")
        try:
            from tools.strategy import Strategy

            df_main = bot.data_service.fetch_and_update_data(sym, "1h")
            df_4h = bot.data_service.fetch_and_update_data(sym, "4h")

            if df_main is None or df_main.empty:
                send_telegram_msg("❌ No hay datos suficientes para explicar.")
                return True

            res = Strategy.analyze(
                df_main,
                df_main,
                bot.brain,
                symbol=sym,
                ghost_model=bot.ghost_model,
                scaler=bot.scaler,
                btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                df_4h=df_4h,
            )
            _, _, _, prob, ind, votos = res

            msg = (
                f"🧐 *EXPLICACIÓN IA: {sym}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *Score Final:* {prob:.1f}/100\n"
                f"👻 *IA (G):* {votos.get('G', 50):.1f}%\n"
                f"📈 *Tendencia (MT):* {votos.get('MT', 50):.1f}%\n"
                f"🧱 *Estructura (SR):* {votos.get('SR', 50):.1f}%\n\n"
                f"📊 *Factores Clave:*\n"
                f"• RSI: {ind['rsi']['val']:.1f}\n"
                f"• ADX: {ind['adx']['val']:.1f}\n"
                f"• Z-Score: {ind.get('z_score', 0):.2f}"
            )
            send_telegram_msg(msg)
        except Exception as error:
            send_telegram_msg(f"❌ Error explicando: {error}")
        return True

    if text == "/archive":
        backup_file = bot.brain.rotate_history()
        send_telegram_msg(f"📦 DB Optimizada. Historial movido a: {backup_file}")
        return True

    if text == "/clean":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nSe bloquea limpieza destructiva durante operación para proteger historial y continuidad."
        )
        return True

    if text == "/unquarantine":
        try:
            with bot.lock:
                cooldown_count = len(bot.cooldown_pairs)
                bot.cooldown_pairs.clear()
                if hasattr(bot, "cooldown_deadlines_mono"):
                    bot.cooldown_deadlines_mono.clear()
            persist_cooldowns(bot)

            blacklist_count = 0
            if hasattr(bot, "risk_engine") and bot.risk_engine is not None:
                if hasattr(bot.risk_engine, "temp_blacklist"):
                    blacklist_count = len(bot.risk_engine.temp_blacklist)
                    bot.risk_engine.temp_blacklist.clear()
                if hasattr(bot.risk_engine, "symbol_streaks"):
                    bot.risk_engine.symbol_streaks.clear()

            send_telegram_msg(
                f"🔓 *COOLDOWNS RESETEADOS*\n\n"
                f"• Cooldowns de pares liberados: {cooldown_count}\n"
                f"• Blacklist anti-revenge liberada: {blacklist_count}\n"
                f"• Estado: listo para re-evaluación inmediata"
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error reseteando cooldowns: {error}")
        return True

    if text.startswith("/force_clear"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_msg("⚠️ Uso: /force_clear [SYMBOL] (ej: /force_clear BTC/USDT)")
            return True

        symbol = normalize_position_symbol(parts[1], default_quote="USDT", strict=True)

        try:
            with bot.lock:
                state = dict((bot.active_trades or {}).get(symbol) or {})

            if not state:
                send_telegram_msg(f"ℹ️ {symbol}: no existe estado activo local para limpiar.")
                return True

            is_simulated = bool(state.get("is_shadow", False) or state.get("simulated_real", False))
            if Config.PAPER_MODE and state.get("simulated_real") is False and not is_simulated:
                bot.is_paused = True
                bot.integrity_lock_active = True
                bot.halt_system_active = True
                send_telegram_msg(
                    f"🛑 /force_clear rechazado en {symbol}: estado no simulado bajo PAPER. "
                    "Requiere reconciliación REAL explícita."
                )
                return True

            entry_coid = str(state.get("entry_client_order_id") or "")
            order_found = False
            position_found = False

            exchange_read_failed = False
            try:
                for order in bot.execution.fetch_open_orders(symbol) or []:
                    if not isinstance(order, dict):
                        continue
                    if str(order.get("clientOrderId") or "") == entry_coid:
                        order_found = True
                        break
                if not order_found and entry_coid:
                    lookup = getattr(bot.execution, "fetch_order_by_client_id", None)
                    if callable(lookup):
                        found = lookup(symbol, entry_coid)
                        order_found = isinstance(found, dict)
            except Exception:
                exchange_read_failed = True

            try:
                for pos in bot.execution.fetch_positions() or []:
                    if not isinstance(pos, dict):
                        continue
                    norm = str(pos.get("symbol") or "").replace(":USDT", "")
                    if norm != symbol:
                        continue
                    if abs(float(pos.get("contracts") or 0.0)) > 0:
                        position_found = True
                        break
            except Exception:
                exchange_read_failed = True

            if exchange_read_failed and not is_simulated:
                bot.is_paused = True
                bot.integrity_lock_active = True
                bot.halt_system_active = True
                send_telegram_msg(
                    f"🛑 /force_clear cancelado en {symbol}: lectura Exchange ambigua. "
                    "HALT activado; ejecuta reconciliación."
                )
                return True

            if order_found or position_found:
                send_telegram_msg(
                    f"🛑 /force_clear cancelado en {symbol}: hay evidencia en Exchange "
                    f"(open_order={int(order_found)} position={int(position_found)}). "
                    "Ejecuta reconciliación, no limpieza manual."
                )
                return True

            keys_to_clear = [
                key
                for key, trade in bot.active_trades.items()
                if isinstance(trade, dict)
                and str(trade.get("symbol") or key).split("|")[0] == symbol
            ] or [symbol]
            with bot.db_lock:
                for key in keys_to_clear:
                    bot.brain.delete_active_trade_state(key)
            with bot.lock:
                for key in keys_to_clear:
                    bot.active_trades.pop(key, None)

            send_telegram_msg(
                f"🧹 FORCE CLEAR aplicado en {symbol}. Estado local y DB liberados sin evidencia en Exchange."
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error en /force_clear {symbol}: {error}")
        return True

    if text == "/thresholds":
        msg = (
            f"🎯 *UMBRALES DE IA (1H)*\n\n"
            f"*Shadow Trades:*\n"
            f"• Rango/Neutral: {Config.SHADOW_MIN_PROBABILITY_RANGE}%\n"
            f"• Tendencia: {Config.SHADOW_MIN_PROBABILITY_TREND}%\n\n"
            f"*Real Trades:*\n"
            f"• Umbral Mínimo: {Config.REAL_CONFIDENCE_MIN * 100}%\n\n"
            f"*Sentimiento Actual:*\n"
            f"• {bot.current_sentiment[0]}\n\n"
            f"_Umbrales más bajos = Más exploración_"
        )
        send_telegram_msg(msg)
        return True

    return False

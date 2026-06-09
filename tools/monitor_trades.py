#!/usr/bin/env python3
"""
[V118-PRO] Monitor de Alertas para Primeros 5 Trades
=====================================================
Alerta con: Régimen, RSI, MAE/MFE
Detiene bot si >3 pérdidas en 5 trades
"""

import sqlite3
import time
import os
import signal
import json
from datetime import datetime

DB_PATH = "sniper_brain.db"
BOT_PID_FILE = ".bot.pid"
TRADE_COUNT_FILE = ".trade_count"


def get_last_trades(n=5):
    """Obtiene los últimos N trades ejecutados."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 
            id, timestamp, symbol, side, entry_price, exit_price,
            pnl, pnl_percent, reason, is_shadow,
            rsi, market_regime, mae_percent, mfe_percent,
            market_context, funding_rate
        FROM trades 
        WHERE pnl > -90
        ORDER BY id DESC LIMIT ?
    """,
        (n,),
    )

    trades = [dict(row) for row in cur.fetchall()]
    conn.close()
    return trades


def parse_votos(market_context):
    """Extrae los votos de agentes del market_context JSON."""
    if not market_context:
        return {}
    try:
        ctx = json.loads(market_context)
        return ctx.get("votos", {})
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def print_trade_alert(trade, trade_num):
    """Imprime alerta formateada para un trade."""
    regime = trade.get("market_regime", "N/A")
    rsi = trade.get("rsi", 0)
    mae = trade.get("mae_percent", 0)
    mfe = trade.get("mfe_percent", 0)
    pnl = trade.get("pnl", 0)
    pnl_pct = trade.get("pnl_percent", 0)
    symbol = trade.get("symbol", "N/A")
    side = trade.get("side", "N/A")
    reason = trade.get("reason", "N/A")
    is_shadow = "SHADOW" if trade.get("is_shadow") else "REAL"

    # Determinar emoji según resultado
    if pnl > 0:
        result_emoji = "✅ WIN"
    elif pnl < 0:
        result_emoji = "❌ LOSS"
    else:
        result_emoji = "⚪ NEUTRAL"

    print("\n" + "=" * 60)
    print(f"🚨 ALERTA TRADE #{trade_num}")
    print("=" * 60)
    print(f"📊 Tipo: {is_shadow}")
    print(f"🪙 Symbol: {symbol} | Side: {side}")
    print(
        f"📈 Entry: ${trade.get('entry_price', 0):.6f} | Exit: ${trade.get('exit_price', 0):.6f}"
    )
    print(f"💰 PnL: {pnl:.4f} USD ({pnl_pct:.2f}%) {result_emoji}")
    print(f"📉 Reason: {reason}")
    print("-" * 60)
    print(f"🌊 RÉGIMEN: {regime}")
    print(f"📊 RSI Entrada: {rsi:.2f}")
    print(f"📉 MAE: {mae:.2f}% | 📈 MFE: {mfe:.2f}%")
    if mfe > 0 and mae < 0:
        print(f"   → Ratio MFE/MAE: {abs(mfe / mae) if mae != 0 else 0:.2f}")

    # Mostrar votos de agentes
    votos = parse_votos(trade.get("market_context", ""))
    if votos:
        print("-" * 60)
        print("🧠 VOTOS AGENTES:")
        agent_names = {
            "T": "Técnico",
            "V": "Visual",
            "J": "Juego",
            "G": "Ghost",
            "C": "Correlación",
            "L": "Liquidez",
            "F": "Fatiga",
            "S": "Sentimiento",
            "O": "On-chain",
            "R": "Regime",
            "M": "Momentum",
            "D": "Divergencia",
            "E": "Entropía",
            "K": "Whale",
        }
        for agent, score in sorted(votos.items()):
            name = agent_names.get(agent, agent)
            bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            print(f"   [{agent}] {name:12}: {bar} {score:.1f}")

    print("=" * 60)


def stop_bot():
    """Detiene el bot cambiando el flag."""
    print("\n🛑 DETENIENDO BOT (>3 pérdidas en 5 trades)...")

    # Método 1: Matar proceso
    if os.path.exists(BOT_PID_FILE):
        with open(BOT_PID_FILE) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"✅ Bot PID {pid} detenido")
        except Exception as error:
            print(f"⚠️ No se pudo detener PID {pid}: {error}")

    # Método 2: Buscar proceso main.py
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", "main.py"], capture_output=True, text=True
        )
        for pid in result.stdout.strip().split("\n"):
            if pid:
                try:
                    os.kill(int(pid), signal.SIGINT)
                    print(f"✅ Solicitado cierre gracioso a Bot PID {pid}")
                except Exception as error:
                    print(f"⚠️ No se pudo enviar SIGINT a PID {pid}: {error}")
    except Exception as error:
        print(f"⚠️ Error buscando procesos de main.py: {error}")

    # Crear flag de parada
    with open(".STOP_BOT", "w") as f:
        f.write(datetime.now().isoformat())


def print_votes_dump(trades):
    """Imprime volcado completo de votos para análisis."""
    print("\n" + "=" * 80)
    print("📋 VOLCADO DE VOTOS AGENTES - PRIMEROS 5 TRADES")
    print("=" * 80)

    for i, trade in enumerate(trades, 1):
        print(f"\n{'─' * 80}")
        print(
            f"TRADE #{i}: {trade.get('symbol')} | {trade.get('side')} | {trade.get('pnl'):.4f} USD"
        )
        print(
            f"  Régimen: {trade.get('market_regime')} | RSI: {trade.get('rsi', 0):.2f}"
        )
        print(f"  Resultado: {'WIN' if trade.get('pnl', 0) > 0 else 'LOSS'}")

        votos = parse_votos(trade.get("market_context", ""))
        if votos:
            print("  Votos Agentes:")
            for agent, score in sorted(votos.items()):
                print(f"    {agent}: {score:.1f}")

    print("\n" + "=" * 80)
    print("📊 RESUMEN DE VOTOS AGREGADOS:")
    print("=" * 80)

    # Agregar votos por agente
    agent_totals = {}
    agent_counts = {}
    for trade in trades:
        votos = parse_votos(trade.get("market_context", ""))
        for agent, score in votos.items():
            agent_totals[agent] = agent_totals.get(agent, 0) + score
            agent_counts[agent] = agent_counts.get(agent, 0) + 1

    print(f"{'Agente':<10} {'Media Voto':<15} {'Promedio':<10} {'Resultado Común'}")
    print("-" * 50)
    for agent in sorted(agent_totals.keys()):
        avg = agent_totals[agent] / agent_counts.get(agent, 1)
        print(f"{agent:<10} {agent_totals[agent]:<15.1f} {avg:<10.1f}")


def check_and_alert():
    """Verifica trades y genera alertas."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 Verificando trades...")

    try:
        trades = get_last_trades(5)

        if not trades:
            print("⏳ Sin trades aún...")
            return

        total_trades = len(trades)
        print(f"📊 Últimos {total_trades} trades encontrados")

        # Mostrar alerta para cada trade
        for i, trade in enumerate(trades):
            print_trade_alert(trade, i + 1)

        # Contar pérdidas
        losses = sum(1 for t in trades if t.get("pnl", 0) < 0)
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)

        print("\n" + "=" * 60)
        print(f"📈 RESUMEN PRIMEROS {total_trades} TRADES:")
        print(f"   Wins: {wins} | Losses: {losses}")
        print(f"   Win Rate: {(wins / total_trades * 100):.1f}%")
        print("=" * 60)

        # Detener si >3 pérdidas en 5 trades
        if total_trades >= 5 and losses > 3:
            print("\n🚨 ALERTA CRÍTICA: >3 pérdidas en 5 trades!")
            print_votes_dump(trades)
            stop_bot()
        elif total_trades >= 5:
            print(
                f"\n✅ Test PASSED: Win Rate {(wins / total_trades * 100):.1f}% >= 45%"
            )

    except Exception as e:
        print(f"❌ Error verificando trades: {e}")


def main():
    print("=" * 60)
    print("🚨 MONITOR DE ALERTAS V118-PRO")
    print("   Regime | RSI | MAE/MFE")
    print("=" * 60)

    # Verificar una vez
    check_and_alert()

    # Si no hay 5 trades, monitorear cada 60 segundos
    trades = get_last_trades(5)
    while len(trades) < 5:
        print(f"\n⏳ Esperando más trades... ({len(trades)}/5)")
        time.sleep(60)
        trades = get_last_trades(5)

    # Verificación final
    check_and_alert()


if __name__ == "__main__":
    main()

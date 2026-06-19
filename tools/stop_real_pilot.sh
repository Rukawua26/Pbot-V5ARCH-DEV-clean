#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Emergency Stop & Rollback — detiene REAL pilot y restaura PAPER mode
# Uso: bash tools/stop_real_pilot.sh [--confirm-real-stop] [--force-kill]
# =============================================================================

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== REAL PILOT SHUTDOWN ==="
echo ""

CONFIRM_REAL_STOP=0
FORCE_KILL=0
for arg in "$@"; do
    case "$arg" in
        --confirm-real-stop) CONFIRM_REAL_STOP=1 ;;
        --force-kill|--emergency) FORCE_KILL=1 ;;
        *)
            echo "Argumento no reconocido: $arg" >&2
            exit 2
            ;;
    esac
done

if [ -f .env ] && grep -Eq '^PAPER_MODE=(false|False|FALSE|0)$' .env; then
    if [ "$CONFIRM_REAL_STOP" -ne 1 ]; then
        echo "🛑 .env indica PAPER_MODE=false."
        echo "Antes de detener REAL, verifica posiciones/órdenes en Binance y HARD SL."
        echo "Reintenta con --confirm-real-stop si aceptas dejar el runtime detenido."
        exit 2
    fi
fi

# 1. Find and kill bot
PID=""
if [ -f logs/bot.pid ]; then
    PID=$(cat logs/bot.pid)
fi

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID=$(pgrep -f "python.*main.py" || true)
fi

if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Deteniendo bot (PID $PID)..."
    
    # Graceful shutdown
    kill -15 "$PID" 2>/dev/null || true
    sleep 5
    
    # Force kill only with explicit confirmation; SIGKILL skips graceful cleanup.
    if kill -0 "$PID" 2>/dev/null; then
        if [ "$FORCE_KILL" -eq 1 ]; then
            echo "⚠️ SIGKILL forzado por --force-kill."
            kill -9 "$PID" 2>/dev/null || true
        else
            echo "🛑 Graceful shutdown timeout. No se envía SIGKILL sin --force-kill."
            exit 3
        fi
    fi
    echo "✓ Bot detenido"
else
    echo "No hay bot activo."
fi

# 2. Restore PAPER backup
if [ -f .env.paper.backup ]; then
    cp .env.paper.backup .env
    echo "✓ .env restaurado desde .env.paper.backup"
else
    echo "⚠️ No hay backup de .env. Debes restaurarlo manualmente."
fi

# 3. Remove PID file
rm -f logs/bot.pid
echo "✓ PID file cleaned"

# 4. Verify
echo ""
echo "=== POST-MORTEM ==="
if [ -f logs/real_pilot.log ]; then
    echo "Últimas líneas del log REAL:"
    tail -5 logs/real_pilot.log
fi

echo ""
echo "Bot detenido y PAPER mode restaurado."
echo "Para reanudar PAPER: python main.py"

#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Start REAL Pilot — arranque controlado con modo REAL
# Uso: bash tools/start_real_pilot.sh [--force]
# =============================================================================

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source "$ROOT/.venv/bin/activate"

# --- Pre-flight checks ---
echo "=== REAL PILOT STARTUP ==="
echo ""

# 1. Verify .env.real exists
if [ ! -f .env.real ]; then
    echo "ERROR: .env.real no encontrado. Cópialo desde .env.real.template primero."
    exit 1
fi
chmod 600 .env.real

# 2. Backup current .env if not already done
if [ ! -f .env.paper.backup ]; then
    cp .env .env.paper.backup
    chmod 600 .env.paper.backup
    echo "✓ Backup .env → .env.paper.backup"
fi

# 3. Check for existing bot PID
if [ -f logs/bot.pid ]; then
    OLD_PID=$(cat logs/bot.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️ Bot activo con PID $OLD_PID. Deteniendo..."
        kill -15 "$OLD_PID" 2>/dev/null || true
        sleep 3
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "⚠️ Graceful shutdown falló, enviando SIGKILL..."
            kill -9 "$OLD_PID" 2>/dev/null || true
            sleep 1
        fi
        echo "✓ Bot anterior detenido"
    fi
fi

# 4. Verify API keys are NOT testnet (user confirmation required)
echo ""
echo "⚠️  IMPORTANTE: Verifica que las API keys en .env.real sean de REAL Binance."
if grep -q '^BINANCE_API_KEY=' .env.real; then
    echo "   API Key presente: BINANCE_API_KEY=****"
else
    echo "   API Key presente: NO ENCONTRADA"
fi
echo ""
if [ "${1:-}" != "--force" ]; then
    read -p "¿Son claves REAL de Binance (no testnet)? Confirma escribiendo 'YES': " CONFIRM
    if [ "$CONFIRM" != "YES" ]; then
        echo "Abortado. No se inició modo REAL."
        exit 1
    fi
fi

# 5. Validate configuration with Python
echo ""
echo "=== Validation ==="
PYTHONPATH="." python -c "
from dotenv import load_dotenv
load_dotenv('.env.real', override=True)
from core.config.manager import Config
errors = Config.validate()
if errors:
    print('Config validation FAILED:')
    for e in errors:
        print(f'  - {e}')
    exit(1)
print('✓ Config validation PASSED')
" 2>&1 || {
    echo ""
    echo "❌ Config validation failed. Abortando."
    exit 1
}

# 6. Verify runtime contracts
echo ""
echo "=== Runtime contract verification ==="
PYTHONPATH="." python tools/regression_contracts.py 2>&1 && echo "✓ Contracts PASSED" || {
    echo "❌ Contract regression FAILED. Abortando."
    exit 1
}

# 7. Activate REAL env
echo ""
echo "=== Starting REAL pilot ==="
mkdir -p logs
cp .env.real .env
chmod 600 .env
echo "✓ .env.real → .env"

# 8. Start bot in background
nohup .venv/bin/python main.py > logs/real_pilot.log 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > logs/bot.pid

echo ""
echo "=== REAL PILOT STARTED ==="
echo "PID: $NEW_PID"
echo "Log: logs/real_pilot.log"
echo "Monitor: tail -f logs/real_pilot.log | grep -E 'ERROR|REAL|CRITICAL|⚠️|🔥'"
echo "Stop:   bash tools/stop_real_pilot.sh"
echo ""

# 9. Quick health check
sleep 5
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "✓ Bot running. Verificando conectividad..."
    PYTHONPATH="." python -c "
import json, time
m = json.loads(open('logs/metrics_summary.json').read())
t = m.get('telemetry', {})
print(f'  Modo: {\"REAL\" if not t.get(\"paper_mode\", True) else \"PAPER\"}')
print(f'  BTC: \${t.get(\"btc_price\", \"?\")}')
print(f'  Regime: {t.get(\"market_regime\", \"?\")}')
print(f'  Balance: {t.get(\"balance\", \"?\")}')
" 2>/dev/null || echo "⚠️ Aún sin métricas — espera ~60s"
fi

echo ""
echo "🔥 REAL PILOT ACTIVO — monitorea en Telegram y logs."

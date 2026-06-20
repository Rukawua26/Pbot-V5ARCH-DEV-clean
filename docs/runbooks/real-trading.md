# Runbook: REAL Trading Activation

Este runbook no es decorativo. Si no puedes completar cada punto, el bot no debe operar en `REAL`.

## Precondiciones Obligatorias

- `.env` no está versionado y no se sube a GitHub.
- API keys reales están IP-restricted y solo tienen permisos Futures necesarios.
- `PAPER_MODE=false` y `ALLOW_REAL_TRADING=true` están definidos conscientemente.
- `MAX_OPEN_TRADES`, `MAX_RISK_USD` y `RISK_PER_TRADE_PERCENT` son conservadores.
- Telegram está configurado y probado.
- Testnet E2E pasó al menos una vez contra la cuenta de testnet.
- `tools/chaos_matrix.py` y `tools/recovery_drill.py` pasan sin fallos.
- No hay posiciones ni órdenes abiertas inesperadas en Binance antes de arrancar.

## Arranque PAPER

```bash
PAPER_MODE=true USE_TESTNET=false ./.venv/bin/python main.py
```

## Arranque Testnet E2E

```bash
RUN_BINANCE_TESTNET_E2E=true \
BINANCE_TESTNET_API_KEY="..." \
BINANCE_TESTNET_API_SECRET="..." \
./.venv/bin/python -m unittest tests.integration.test_binance_testnet_execution_flow
```

## Arranque REAL Manual

```bash
PAPER_MODE=false \
ALLOW_REAL_TRADING=true \
USE_TESTNET=false \
MAX_OPEN_TRADES=1 \
MAX_RISK_USD=5 \
RISK_PER_TRADE_PERCENT=0.25 \
./.venv/bin/python main.py
```

## Arranque REAL con systemd user

```bash
SNIPER_ENV_FILE="/ruta/segura/.env" bash tools/install_watchdog_systemd.sh
systemctl --user status sniper-ai.service
journalctl --user -u sniper-ai.service -f
```

## Checks Inmediatos Tras Arranque

- Log contiene `MODO REAL ACTIVADO` y no contiene `CONFIG_VALIDATION_FAILED`.
- Telegram recibe alerta/estado operativo.
- Binance no muestra posiciones huérfanas.
- `logs/execution_events.jsonl` registra eventos si hay señales.
- Si aparece `HALT`, no reinicies a ciegas. Sigue `docs/runbooks/recovery.md`.

## Credenciales Revocadas o Permisos Invalidos

- En `REAL`, un fallo de autenticacion o permisos debe abortar o mantener `HALT`.
- No cambies a endpoints publicos para seguir operando en `REAL`.
- El runtime ejecuta un health check periodico de auth con `fetch_balance`; si detecta fallo compatible con credenciales/permisos invalidos, activa `HALT`.
- Para desactivar o espaciar el check, usa `REAL_AUTH_HEALTHCHECK_INTERVAL_SECONDS`; no lo pongas en `0` para operar REAL sin monitoreo de auth.
- Rota API keys manualmente desde Binance, aplica IP restriction y permisos Futures minimos.
- Despues de rotar keys, repite Testnet E2E, chaos matrix y recovery drill antes de reactivar riesgo.

## Gates Previos a REAL

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/chaos_matrix.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/recovery_drill.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

## Regla de Escalado

No subas riesgo después de una operación ganadora. Solo escala si hay evidencia fuera de muestra y operación estable durante días, no por euforia.

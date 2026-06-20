# Runbook: High Confidence Signals With No Entry

## Sintomas

- Senales de 70% o mas no abren operacion.
- Logs muestran veto por ML, shock, SL excesivo, simbolo en cuarentena o precondiciones runtime.

## Checks Seguros

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/intelligence/report_daily.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/shadow_readiness_gate.py
```

## Causas Frecuentes

- `REQUIRE_GHOST_MODEL_FOR_TRADING=True` sin modelo cargado.
- `MAX_ENTRY_SL_PCT` demasiado bajo para ATR actual.
- `SHOCK_MIN_DIST_PCT` demasiado alto para el regimen.
- `HALT_SYSTEM_ACTIVE`, `INTEGRITY_LOCK_ACTIVE` o simbolo en cuarentena.

## Decision Operativa

- No bajes guardrails en REAL para forzar entradas.
- Primero confirma si el bloqueo viene de riesgo runtime o de estrategia.
- Cambios de umbral deben validarse en SHADOW antes de REAL.

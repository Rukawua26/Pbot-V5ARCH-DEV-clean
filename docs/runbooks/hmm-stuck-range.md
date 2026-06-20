# Runbook: HMM Stuck in RANGE

## Sintomas

- El estado HMM permanece en `RANGE` durante ventanas prolongadas aunque BTC muestra tendencia clara.
- Las senales quedan penalizadas o vetadas de forma repetida.
- `logs/execution_events.jsonl` muestra muchos eventos `MARKOV_REGIME_DECISION` o `RANGE_VETO`.

## Checks Seguros

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regime_scorecard.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/intelligence/report_daily.py
```

## Decision Operativa

- No ajustes umbrales en caliente si hay posiciones REAL abiertas.
- Si el bot abre operaciones contra el regimen esperado y el drawdown aumenta, activa `HALT` manual.
- Si solo afecta a SHADOW, conserva el modo observacion y revisa el scorecard antes de cambiar config.

## Criterio Para Volver

- HMM publica snapshot reciente.
- Scorecard no muestra divergencia persistente entre regimen y resultados.
- Chaos matrix y tests focalizados pasan.

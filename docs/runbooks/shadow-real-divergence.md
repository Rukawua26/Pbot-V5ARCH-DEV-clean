# Runbook: SHADOW vs REAL Divergence

## Sintomas

- SHADOW gana pero REAL pierde con senales similares.
- Hay diferencias grandes de slippage, fills parciales o rechazos.
- `shadow_real_gap` aparece en advisories.

## Checks Seguros

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/intelligence/report_daily.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/chaos_matrix.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/recovery_drill.py
```

## Decision Operativa

- No promociones reglas desde SHADOW si la matriz de chaos falla.
- Si REAL presenta fills ambiguos, prioriza `HALT` y reconciliacion.
- Ajusta simulacion SHADOW solo despues de comparar eventos reales de ejecucion.

## Criterio Para Volver

- Chaos matrix `failed == 0`.
- Recovery drill `ok == true` y `within_target == true`.
- La divergencia tiene causa documentada o queda dentro del rango tolerado.

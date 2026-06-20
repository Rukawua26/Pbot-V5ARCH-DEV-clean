# Runbook: Drawdown While Still Trading

## Sintomas

- PnL diario cae mas de lo esperado y el bot sigue evaluando entradas.
- Hay eventos de perdida acumulada sin `HALT_SYSTEM_ACTIVE`.
- Telegram o dashboard muestran operaciones nuevas durante drawdown.

## Checks Seguros

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/risk_decision_report.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/runtime_metrics_summary.py
```

## Decision Operativa

- Si hay exposicion REAL, Binance es la fuente de verdad.
- Verifica posiciones, ordenes reduce-only y HARD SL en Binance antes de reiniciar.
- Si no puedes verificar balance o posiciones, mantener `HALT`.

## Criterio Para Volver

- Exposicion real es cero o esta protegida por HARD SL.
- `docs/runbooks/recovery.md` esta completado.
- Se agrego test de regresion si el breaker no bloqueo como debia.

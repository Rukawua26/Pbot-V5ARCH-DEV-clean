# Plan de Recuperacion de Entradas

Estado: implementacion completada; pendiente observacion PAPER/SHADOW de 24-48 horas.

## Objetivo

Determinar por que el bot dejo de crear entradas y recuperar el flujo sin debilitar
las protecciones de PAPER/SHADOW/REAL.

## Orden de ejecucion

1. Congelar la evidencia y confirmar las rutas reales de DB, logs y telemetria.
2. Ejecutar un replay contrafactual de las 721 señales recientes con:
   - configuracion actual;
   - `BULL_TREND_ENTRY_VETO_ENABLED=false`;
   - veto BULL corregido por direccion;
   - coherencia desactivada.
3. Ejecutar en PAPER/SHADOW un experimento de una sola variable:
   `BULL_TREND_ENTRY_VETO_ENABLED=false`.
4. Mantener intactos durante el experimento:
   - `PAPER_MODE=true`;
   - `ALLOW_REAL_TRADING=false`;
   - HARD SL;
   - veto RANGE;
   - `MIN_ATR_PCT`;
   - RRR;
   - cooldowns y limites de exposicion.
5. Si no reaparecen entradas, probar la coherencia en un experimento separado.
6. Corregir el veto BULL para bloquear solo la direccion contraria a la tendencia.
7. Investigar `DATA_INTEGRITY_FAIL`, timeouts, errores de analisis y backoff cuando
   el universo de candidatos este vacio.
8. Validar durante 24-48 horas en PAPER/SHADOW.
9. Acumular 50-100 trades SHADOW cerrados antes de ajustar score o promover cambios.

## Resultado de implementacion

- El replay de las ultimas 721 alertas confirmo 721 bloqueos observados.
- `bull-off` liberaria 565 `SELL` contra tendencia BULL; se descarto para runtime.
- El veto BULL se hizo direccional en PAPER: permite `BUY` alineado y mantiene bloqueado `SELL`.
- En REAL, `BUY` BULL sigue bloqueado salvo promocion explicita con
  `BULL_TREND_ALIGNED_REAL_ENABLED=true`; el default es `false`.
- El desacuerdo HMM/sentimiento puede observarse con
  `REGIME_CONFLICT_SHADOW_OVERRIDE_ENABLED=true`, pero solo con HMM fresco y
  `PAPER_MODE=true`; el default versionado permanece `false`.
- Los timeouts, fetch errors y errores del pipeline ahora tienen causas diferenciadas.
- `signal_alerts.status` vuelve a actualizarse despues de eliminar la columna legacy
  `trade_id`.
- Dependencias vulnerables se actualizaron a versiones corregidas.

## Siguiente observacion

1. Mantener `PAPER_MODE=true` y `ALLOW_REAL_TRADING=false`.
2. Activar solo `REGIME_CONFLICT_SHADOW_OVERRIDE_ENABLED=true` si el desacuerdo de
   clasificadores vuelve a producir cero entradas.
3. Medir durante 24-48 horas antes de modificar otro filtro.
4. No habilitar `BULL_TREND_ALIGNED_REAL_ENABLED` antes de completar 50-100 trades
   SHADOW cerrados y revisar PF, WR, HARD SL y drawdown.

## Criterios de seguridad

- No operar en REAL.
- No activar Ghost ni `REQUIRE_GHOST_MODEL_FOR_TRADING`.
- No desactivar HARD SL, reconciliacion, HALT, limites ni controles de riesgo.
- No cambiar varios filtros en el mismo experimento.

## Metricas obligatorias

- razones de veto por ventana temporal;
- `ORDER_INTENT_CREATED`;
- trades SHADOW abiertos y cerrados;
- errores `DATA_INTEGRITY_FAIL`;
- timeouts y latencia del ciclo;
- ausencia de eventos REAL.

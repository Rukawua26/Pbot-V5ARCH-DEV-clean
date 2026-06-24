# Mejoras Pendientes

Documento vivo para registrar mejoras, integraciones y decisiones tecnicas pendientes del proyecto. Esta es la fuente de verdad cuando se pregunte que mejoras estan pendientes.

## Reglas De Trabajo

- No tocar runtime critico sin tests enfocados y validacion minima.
- Mantener separacion estricta entre `PAPER`, `SHADOW` y `REAL`.
- En `REAL`, cualquier estado ambiguo debe preferir `HALT` y reconciliacion antes de continuar.
- No introducir logica nueva de ejecucion fuera de `core/execution_adapters.py` y los flujos existentes.
- No integrar senales al `Risk Engine`, sizing, entradas o salidas sin evidencia estadistica.
- No refactorizar modulos que ya funcionan salvo que exista un problema concreto, medible y cubierto por tests.
- Si una mejora es experimental, debe iniciar apagada por defecto y validarse primero en `PAPER` o `SHADOW`.

## Estado Actual Confirmado

- Runtime safety gates integrados en CI.
- Coverage gate elevado a `75%`.
- Chaos matrix integrada: `tools/chaos_matrix.py`.
- Recovery drill integrado: `tools/recovery_drill.py`.
- Telemetria runtime local JSONL integrada.
- Health check de auth `REAL` integrado con comportamiento `HALT` ante auth/permisos invalidos.
- README actualizado con `949 tests OK`, `2 skipped` y `75% coverage`.
- Commit confirmado y subido a GitHub: `ba5a42a harden: add runtime safety gates and raise coverage`.
- **FVG Tracker (GapTrackerModule)** implementado como modulo satelite read-only en `core/analytics/fvg_tracker.py`.

## Mejoras Pendientes

### 1. FVG Tracker — Medicion estadistica en PAPER/SHADOW

FVG Tracker ya implementado. Pendiente:

1. Activar `FVG_TRACKER_ENABLED=true` en PAPER o SHADOW.
2. Recolectar metricas de calidad de alertas (falsos positivos, timing util).
3. Medir si mejora MAE/MFE, winrate o reduce entradas malas.
4. Si solo genera ruido, mantener como herramienta observacional.

Criterio de exito: informacion incremental medible sobre trades existentes. Sin evidencia, no integrar al Risk Engine ni a ejecucion.

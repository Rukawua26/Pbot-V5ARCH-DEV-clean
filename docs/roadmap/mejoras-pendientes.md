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

## Mejoras Pendientes

### 1. GapTrackerModule / FVG Tracker

Estado: Propuesta en evaluacion.

Objetivo:
Detectar gaps o Fair Value Gaps en temporalidad `1h`, persistirlos entre reinicios y generar alertas de proximidad sin afectar la ejecucion.

Decision actual:
La mejora es factible, pero no debe integrarse todavia al `Risk Engine`, sizing, entradas ni salidas. Primero debe operar como modulo satelite read-only, apagado por defecto, para medir si aporta valor real.

Problemas detectados en el RFC inicial:

- `core/bot_facade.py` no expone una fachada real de notificaciones; actualmente es un alias legacy a `Bot`.
- El flujo WebSocket real no coincide con el diagrama propuesto: existen `core/bot_io_loops.py` para ticker stream y `tools/ws_manager.py` para depth/aggTrade.
- Persistir `active_gaps.json` sin escritura atomica puede corromper estado ante crash.
- Hacer REST historico amplio en startup puede consumir API weight y retrasar arranque.
- Alertas al `1%` y `0.5%` pueden generar ruido si no hay throttling global y flags por gap.
- FVG/gaps no son edge por si solos; requieren medicion estadistica antes de convertirse en senal operativa.

Diseno recomendado:

- Crear `core/analytics/gap_tracker.py` para la logica pura de deteccion y proximidad.
- Crear `core/analytics/gap_store.py` para persistencia aislada.
- Usar JSON atomico como MVP: escribir a archivo temporal y reemplazar con `os.replace`.
- Mantener un lock propio del tracker; no usar `bot.price_lock` para persistencia o notificaciones.
- Evaluar precios fuera del lock caliente del WebSocket.
- Usar `tools.notifier.send_telegram_msg` o callback inyectado, no ampliar `core/bot_facade.py` sin necesidad.
- Agregar flags en configuracion, con `GAP_TRACKER_ENABLED = False` por defecto.
- Reescanear estructura `1h` con `data_service.fetch_and_update_data(symbol, "1h")` cuando sea posible, evitando REST crudo nuevo.

Definicion tecnica pendiente:

- Bullish FVG: definir formalmente con tres velas antes de implementar.
- Bearish FVG: definir formalmente con tres velas antes de implementar.
- Zona, invalidacion, fill parcial, fill total y expiracion deben tener reglas explicitas.

Proximos pasos:

1. Definir matematicamente FVG bullish y bearish.
2. Crear tests puros con velas `1h` sinteticas.
3. Implementar store JSON atomico con tests de archivo vacio, corrupto y reemplazo seguro.
4. Implementar evaluacion de proximidad con thresholds configurables.
5. Integrar en modo pasivo y apagado por defecto.
6. Activar en `PAPER` o `SHADOW` para recolectar metricas.
7. Medir si mejora MAE/MFE, winrate, timing o reduce entradas malas antes de considerar uso operativo.

Criterio de exito:
La mejora solo se considera beneficiosa si demuestra informacion incremental medible sobre trades existentes o calidad de decision. Si solo genera ruido, debe quedarse como herramienta observacional o eliminarse.

Validacion minima al implementarla:

- Tests unitarios del detector.
- Tests de persistencia atomica.
- Tests de throttling de alertas.
- Test de integracion del hook WebSocket sin bloqueo ni escritura por tick.
- `./.venv/bin/python -m compileall -q main.py core tools`.
- `PATH="./.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh` si toca imports/bootstrap.
- `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py` si toca `Bot`, `BotFacade` o contratos publicos.

# Memoria Tecnica Del Proyecto

Fuente versionada para cambios criticos, decisiones de diseno, invariantes y regresiones prevenidas. Antes de tocar codigo, revisa este documento junto con `AGENTS.md`.

## Uso Obligatorio

- Antes de modificar codigo, revisar esta memoria tecnica con lectura proporcional al riesgo del cambio.
- Si el cambio toca bugs ya conocidos, revisar tambien `.opencode/context/known-bugs.md`.
- Si el cambio implementa una mejora futura, revisar `docs/roadmap/mejoras-pendientes.md`.
- Si el cambio toca ejecucion, reconciliacion, wallet sync, watchdog, recovery, ordenes, posiciones, `HALT`, stop loss, `core/bot_app.py`, `core/bot_facade.py`, `core/bot_connection.py` o `core/execution_adapters.py`, tratarlo como runtime critico.
- Registrar aqui cualquier cambio que cree una regla preventiva, corrija una regresion, modifique contratos publicos o cambie el comportamiento operativo.

## Como Leer Este Documento Ahorrando Contexto

- Para preguntas generales o analisis sin edicion, no leas todo el documento salvo que se pidan decisiones historicas.
- Para cambios menores no criticos, revisa `Uso Obligatorio`, `Invariantes` y solo las entradas relacionadas con el archivo o area afectada.
- Para mejoras pendientes, revisa tambien `docs/roadmap/mejoras-pendientes.md`.
- Para runtime critico, revisa invariantes completos, entradas criticas recientes, `.opencode/context/known-bugs.md` y la skill runtime.
- Para refactors amplios o cambios de contratos publicos, revisa todo este documento antes de editar.
- No conviertas esta memoria en un diario: registra decisiones, reglas preventivas, archivos sensibles y validacion; no pegues logs largos, diffs ni outputs completos.
- Si este archivo supera un tamano operativo razonable, archiva historico por trimestre en `docs/engineering/archive/` y deja aqui solo el indice activo.

## Invariantes Que No Deben Romperse

- El exchange manda sobre la DB para exposicion real y estado de ordenes/posiciones.
- No dejar posiciones reales sin `HARD SL`.
- En `REAL`, estado live ambiguo debe preferir `HALT` y reconciliacion antes de continuar.
- No degradar `REAL` a endpoints publicos ante fallos de auth/permisos.
- No agregar retries no idempotentes que puedan duplicar exposicion.
- Mantener separacion estricta entre `PAPER`, `SHADOW` y `REAL`.
- No introducir `pass` silenciosos en `core/`; el guard de CI debe seguir bloqueandolos.
- No mover similarity search despues del sizing en `core/trade_entry.py`; el `similarity_boost` debe poder afectar la posicion.
- No bajar `MAX_ENTRY_SL_PCT` por debajo de `3.0` sin validar ATR promedio de simbolos objetivo.
- No subir `SHOCK_MIN_DIST_PCT` por encima de `0.2` sin medir falsos positivos.
- No activar `REQUIRE_GHOST_MODEL_FOR_TRADING=True` sin verificar que `ghost_model` existe y que el rechazo queda visible en logs.
- No subir `MIN_NOTIONAL_VALUE` sin verificar balance por leverage; mantener env-override.

## Cambios Criticos Registrados

### 2026-07-08 - Gate institucional y reconciliacion post-WebSocket

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `core/risk_policy.py`: se agrega decision estructurada `NEUTRAL_AGENT_VOTE` para bloquear ejecucion cuando todos los agentes devuelven `50.0` y `prob_final=50.0`.
- `core/risk_policy.py`: se bloquean nuevas entradas REAL mientras `ws_reconciliation_in_progress` esta activo.
- `core/ws_reconciliation.py`: nuevo coordinador de reconciliacion post-reconexion WebSocket; en PAPER solo registra/omite, en REAL ejecuta reconciliacion REST con debounce y activa proteccion/HALT si falla.
- `tools/ws_manager.py`: el WebSocket L2/CVD acepta callback `on_reconnect` y lo dispara solo tras reconexion exitosa.
- `core/bot_io_loops.py`: el WebSocket ticker emite eventos estructurados de conexion/desconexion y dispara reconciliacion tras reconectar.
- `core/bot_initialization.py`: cablea el callback de reconexion al bot runtime.

Reglas preventivas:

- No crear un `RiskManager` paralelo; extender `core/risk_policy.py` y `core/risk_engine.py` para mantener una unica frontera de riesgo.
- No permitir nuevas entradas REAL durante reconciliacion post-WebSocket.
- No disparar reconciliacion por errores de conexion; solo tras reconexion confirmada.
- En REAL, si la reconciliacion post-WebSocket falla o queda ambigua, preferir `HALT`/proteccion runtime antes de continuar.
- Mantener debounce para evitar tormentas REST tras microcortes.

Validacion registrada:

- Tests enfocados `test_risk_policy`, `test_ws_reconciliation`, `test_cvd_filter` OK.
- Tests de entrada/runtime `test_execute_order_coverage` y `test_runtime_safety_regressions` OK.
- `compileall main.py core` OK.
- `ruff check core/ tests/` OK.
- `ruff format --check core/ tests/` OK.
- `scripts/smoke_modular_imports.sh` OK.
- `tools/check_no_silent_pass.py` OK.
- `tools/regression_contracts.py` OK.
- `tools/chaos_matrix.py`: 8/8 OK.
- `tools/recovery_drill.py`: 3/3 OK.
- Suite unitaria completa: `1098` tests OK, `2` skipped.

### 2026-07-06 - Fixes runtime criticos TP1, chase exit y daily drawdown

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `core/bot_guardian.py`: TP1 queda encapsulado en `_handle_tp1`; en `PAPER`/`SHADOW` solo simula estado local y nunca envia `create_reduce_only_market_order`.
- `core/bot_guardian.py`: en `REAL`, TP1 solo reduce `amount`/`size_usd` y marca `tp1_triggered` si la orden reduce-only queda confirmada como filled/closed.
- `core/bot_guardian.py`: si TP1 real falla o queda ambiguo, activa `HALT`, persiste `TP1_EXIT_AMBIGUOUS` y emite eventos runtime.
- `core/execution_order_helpers.py`: si falla `cancel_order` en chase exit y no puede verificarse `fetch_open_orders`, retorna `STUCK` y no crea otra orden de salida.
- `core/risk_engine.py`: `check_daily_drawdown()` calcula el porcentaje canonico desde `usd_hoy/current_balance`, evitando mezclar fraccion con porcentaje.
- Tests de regresion agregados en `tests/test_runtime_safety_regressions.py`, `tests/test_execution_service_helpers.py` y `tests/test_risk_engine_coverage.py`.

Reglas preventivas:

- TP1 en `PAPER`/`SHADOW` no debe tocar exchange live aunque existan API keys.
- Nunca mutar estado local de parcial real si la reduccion no esta confirmada por exchange.
- Ante cancelacion ambigua de orden de salida, detener la persecucion y devolver `STUCK`; no colocar una nueva orden hasta reconciliar.
- Daily drawdown debe comparar unidades homogeneas: porcentaje contra porcentaje o fraccion contra fraccion; preferir calcular desde USD y balance.

Validacion registrada:

- `ruff check core/ tests/` OK.
- `ruff format --check core/ tests/` OK.
- `compileall main.py core` OK.
- `mypy` superficie CI OK.
- `tools/check_no_silent_pass.py` OK.
- `tools/regression_contracts.py` OK.
- `tools/chaos_matrix.py`: 8/8 OK.
- `tools/recovery_drill.py`: 3/3 OK.
- Suite unitaria completa: `1030` tests OK, `2` skipped.

### 2026-06-20 - Runtime safety gates y coverage 75%

Commit: `ba5a42a harden: add runtime safety gates and raise coverage`

Que cambio:

- Se integro `tools/chaos_matrix.py` como matriz determinista de fallos de exchange.
- Se integro `tools/recovery_drill.py` para validar restart safety.
- Se elevo el coverage gate a `75%` en `pyproject.toml` y CI.
- Se agrego telemetria runtime local JSONL en `core/runtime_metrics.py` y resumen en `tools/runtime_metrics_summary.py`.
- Se agrego health check de auth `REAL` en `core/real_auth_health.py` con `HALT` ante fallos de auth/permisos.
- Se conecto el health check en `core/bot_runtime.py`.
- Se instrumentaron eventos de exchange calls, `HALT`, `HARD SL`, guardian errors, recovery drill y auth health.
- Se reforzaron tests runtime criticos y cobertura de modulos operativos.
- Se movieron unidades systemd historicas a `deploy/systemd/legacy/`.
- Se elimino `Propuestas/` del repo versionado y se agrego a `.gitignore`.
- README quedo actualizado a `949 tests OK`, `2 skipped`, `75% coverage`.

Archivos sensibles:

- `.github/workflows/ci.yml`
- `pyproject.toml`
- `core/bot_runtime.py`
- `core/real_auth_health.py`
- `core/runtime_metrics.py`
- `core/execution_service.py`
- `core/bot_guardian.py`
- `core/trade_exit.py`
- `tools/chaos_matrix.py`
- `tools/recovery_drill.py`
- `tools/runtime_metrics_summary.py`

Reglas preventivas:

- No bajar el coverage gate por debajo de `75` sin razon explicita y commit que lo justifique.
- No quitar chaos matrix ni recovery drill de CI sin reemplazo equivalente.
- En `REAL`, auth/permisos invalidos deben abortar o activar `HALT`; no deben degradar silenciosamente.
- Si un cierre real queda ambiguo, conservar la prioridad `EXIT_STUCK` + `HALT`.
- Los eventos metricos no deben bloquear ejecucion ni escribir en paths sensibles.

Validacion registrada:

- `949` tests OK, `2` skipped.
- Coverage total `75%`.
- Chaos matrix: `8/8` escenarios OK.
- Recovery drill: `3/3` escenarios OK.
- `compileall`, smoke modular imports, no silent pass, regression contracts, temporal invariance y audit correlacional OK.

### 2026-06-20 - Roadmap de mejoras pendientes

Commit: `c6fb203 docs: add pending improvements roadmap`

Que cambio:

- Se creo `docs/roadmap/mejoras-pendientes.md` como fuente de verdad para mejoras futuras.
- Se documento `GapTrackerModule / FVG Tracker` como propuesta viable pero no lista para tocar ejecucion.
- Se enlazo el roadmap desde `README.md`.

Reglas preventivas:

- Cuando se pregunte por mejoras pendientes, leer `docs/roadmap/mejoras-pendientes.md` antes de responder.
- `GapTrackerModule` debe empezar como modulo satelite read-only, apagado por defecto.
- No integrar FVG/gaps al `Risk Engine`, sizing, entradas o salidas sin evidencia estadistica.
- No ampliar `core/bot_facade.py` solo para notificaciones del tracker; usar callback inyectado o `tools.notifier` salvo decision arquitectonica explicita.
- Persistencia inicial del tracker, si se implementa, debe ser atomica y no escribir por tick.

### 2026-06-20 - Memoria tecnica obligatoria

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- Se crea este ledger tecnico para que agentes y humanos revisen cambios criticos antes de modificar codigo.
- Se actualiza `AGENTS.md` para exigir revisar este documento antes de tocar codigo.
- Se enlaza esta memoria desde `README.md`.

Reglas preventivas:

- Si un cambio toca un archivo mencionado en esta memoria, revisar la entrada correspondiente antes de editar.
- Si una correccion nueva evita una regresion, agregar una entrada aqui o en `.opencode/context/known-bugs.md` segun alcance.
- Si una decision deja una mejora pendiente, registrar el detalle en `docs/roadmap/mejoras-pendientes.md` y referenciarlo desde aqui cuando sea critico.

## Pendiente De Registro

- Mantener este archivo actualizado con cada commit que cambie runtime critico, contratos publicos, reglas de riesgo, validacion CI o bugs preventivos.
- Si el archivo crece demasiado, archivar entradas antiguas por trimestre en `docs/engineering/archive/` y conservar aqui el indice.

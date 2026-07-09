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

### 2026-07-09 - Sprint 4 cuantitativo: escala y sensibilidad pasiva

Commit: `a501874 feat: add passive slope telemetry and triage scale preset`

Que cambia:

- `core/config/operational.py` y `core/market_intelligence.py`: preset triage gradual `TRIAGE_CANDIDATE_POOL_MULTIPLIER=2` y `TRIAGE_MAX_CANDIDATE_POOL=60`.
- `core/config/manager.py`: agrega `EMA_SLOPE_COMPARISON_ENABLED` y `EMA_SLOPE_COMPARISON_LOOKBACK=4`.
- `core/strategy/utils.py`: mantiene `ema50_slope` con `EMA_SLOPE_LOOKBACK=2` para ejecucion y calcula pasivamente `ema50_slope_alt` con lookback comparativo.
- `core/signals/context.py`: propaga `ema50_slope_alt` y `ema50_slope_alt_lookback` al contexto para analisis SHADOW.
- Tests cubren perfil triage `2/60` y telemetria pasiva de slope.

Reglas preventivas:

- No cambiar `EMA_SLOPE_LOOKBACK` de ejecucion por intuicion; usar `ema50_slope_alt` para comparar pasivamente.
- No subir `TRIAGE_MAX_CANDIDATE_POOL` por encima de 60 sin medir timeouts, latencia de ciclo y ratio de vetos posteriores.
- `ema50_slope_alt` no debe alimentar filtros ni sizing sin evidencia SHADOW.

Validacion registrada:

- `tests/test_strategy_utils.py`, `tests/test_market_intelligence_and_balance.py`, `tests/test_runtime_snapshot_cache.py` OK.

### 2026-07-09 - Sprint 3 cuantitativo: genetica en batch

Commit: `3af0f93 feat: batch genetic evolution outside trade close`

Que cambia:

- `core/trade_exit.py`: elimina `bot.brain.evolve_genetics(symbol)` del cierre inmediato; ahora encola el simbolo en `_genetic_batch_pending_symbols` y emite `GENETIC_BATCH_QUEUED`.
- `core/bot_maintenance.py`: agrega `run_genetic_batch(...)`, invocado desde `check_for_evolution(...)`, con minimo de muestras por simbolo y eventos `GENETIC_BATCH_STARTED`, `GENETIC_BATCH_COMPLETED`, `GENETIC_BATCH_SKIPPED`, `GENETIC_BATCH_SWAP_APPLIED`.
- `core/config/manager.py`: agrega `GENETIC_BATCH_ENABLED` y `GENETIC_BATCH_MIN_TRADES` con validacion.
- `tests/test_trade_exit.py`: cubre cierre sin evolucion inmediata y batch genetico suficiente/insuficiente/deshabilitado.

Reglas preventivas:

- No volver a llamar `evolve_genetics(symbol)` desde el hot-path de cierre.
- Mantener `update_trade_context_result`, `finalize_confidence_exit_audit` y `update_agent_reputation` por trade.
- Si se refactoriza `Brain.evolve_genetics`, calcular parametros en copia aislada y aplicar el swap de forma corta y trazable.

Validacion registrada:

- `tests/test_trade_exit.py` OK.

### 2026-07-09 - Sprint 2 cuantitativo: pre-filtros baratos de señales

Commit: `032a8d9 feat: add cheap signal prefilters`

Que cambia:

- `core/bot_signals.py`: agrega `_passes_cheap_pre_filters(...)` y `_record_cheap_prefilter_veto(...)` para descartar simbolos antes de `_analyze_symbol_candidate(...)`.
- `core/bot_signals.py`: el pre-filtro corre tambien antes de `_precompute_signal_analysis(...)`, evitando analisis paralelo innecesario.
- `core/bot_signals.py`: registra `CHEAP_PREFILTER_VETO` con razon normalizada y actualiza radar antes del consenso pesado.
- `tests/test_bot_signal_scan_cycle.py`: cubre simbolo bloqueado, simbolo activo y latencia extrema sin invocar `_analyze_symbol_candidate(...)`.

Reglas preventivas:

- No mover RSI, ADX, shock distance, coherencia final, MTF/OI ni filtros dependientes de `ctx` profundo al pre-filtro barato.
- El precompute no debe mutar cuarentena de latencia; solo el loop principal debe registrar/mutar estado visible.
- Mantener razones normalizadas para `CHEAP_PREFILTER_VETO`: `COOLDOWN_ACTIVE`, `SYMBOL_ALREADY_ACTIVE`, `LATENCY_QUARANTINED`, `DATA_INTEGRITY_FAIL`, `SYMBOL_BLOCKED`.

Validacion registrada:

- `tests/test_bot_signal_scan_cycle.py` OK.

### 2026-07-09 - Sprint 1 cuantitativo: filtro RRR estructural

Commit: `6c9659d feat: add structural risk reward entry filter`

Que cambia:

- `core/trade_entry.py`: agrega `_evaluate_risk_reward_filter()` y bloquea entradas con RRR estructural insuficiente despues de calcular `sl_val`/`tp_val` y antes de similarity/sizing/ejecucion.
- `core/trade_entry.py`: el RRR usa entrada estimada defensiva con `spread` + `MAX_SLIPPAGE`; BUY penaliza entrada hacia arriba y SELL hacia abajo.
- `core/trade_entry.py`: emite evento estructurado `RISK_REWARD_VETO` con entrada estimada, SL, TP, risk, reward, RRR real/requerido, spread y `atr_pct`.
- `core/config/manager.py`: agrega flags `RISK_REWARD_FILTER_ENABLED`, `MIN_RISK_REWARD_RATIO`, `RISK_REWARD_VOLATILITY_BOOST_ENABLED`, `RISK_REWARD_HIGH_VOL_MIN_RATIO` y validacion de umbrales.
- `core/signals/execution.py`: muestra `RISK_REWARD_VETO` como veto visible en dashboard/radar.
- `tests/test_execute_order_coverage.py`: cubre BUY/SELL valido/invalido, bounds invalidos, penalizacion por spread/slippage y aborto antes de persistir intencion.

Reglas preventivas:

- No mover este filtro despues del sizing ni despues de `similarity_boost`; debe cortar trades estructuralmente malos antes de calcular tamano.
- No bajar `MIN_RISK_REWARD_RATIO` por debajo de `1.5` sin medicion PAPER/SHADOW cerrada.
- Si se cambia la formula de entrada estimada, mantener penalizacion conservadora para spread/slippage y tests BUY/SELL.
- `RISK_REWARD_VETO` debe seguir visible como evento estructurado y como veto de UI/radar.

Validacion registrada:

- `tests/test_execute_order_coverage.py` OK.

### 2026-07-08 - Fase 4 institucional: healthcheck avanzado, alertas proactivas y VPS checklist

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `tools/dashboard_api_server.py`: endpoint `/api/v1/health` ahora verifica freshness del snapshot (healthy/degraded/unhealthy), flags de proteccion (HALT, circuit breaker, WS reconcile) y devuelve JSON estructurado sin requerir API key.
- `core/bot_runtime_safety.py`: alerta proactiva `DAILY_DRAWDOWN_WARNING` al 80% del limite diario, envia evento + Telegram, con flag `_drawdown_warning_sent` que se resetea diariamente.
- `core/risk_policy.py`: alerta proactiva `CIRCUIT_BREAKER_TRIGGER_ALERT` la primera vez que se dispara el circuit breaker, con flag `_circuit_breaker_alert_sent` que se resetea diariamente.
- `core/bot_initialization.py`, `core/bot_balance_ops.py`, `core/command_router.py`: reset diario de `_drawdown_warning_sent` y `_circuit_breaker_alert_sent`.
- `ops/vps-checklist.md`: nuevo checklist de despliegue VPS con Docker, variables .env, healthcheck, logs, backup y troubleshooting.
- `tests/test_drawdown_warning.py`: nuevos tests para drawdown warning (80%, <80%, solo una vez).
- `tests/test_risk_policy.py`: nuevos tests para circuit breaker hook (primer trigger, segundo trigger silenciado).
- `tests/test_daily_circuit_breaker.py`: actualizado para silenciar hook en tests existentes.

Reglas preventivas:

- No remover el endpoint `/api/v1/health` sin reemplazarlo; es el healthcheck de Docker/VPS.
- No cambiar el umbral de 80% del drawdown sin validar que no genere falsos positivos.
- No remover las alertas proactivas; son la zona de amortiguacion antes del HALT.
- El flag `_drawdown_warning_sent` y `_circuit_breaker_alert_sent` deben resetearse en `bot_balance_ops.py` y `command_router.py` al igual que `daily_drawdown_alert_sent`.
- El endpoint `/api/v1/health` no requiere API key para que Docker pueda sondearlo.

Validacion registrada:

- Tests enfocados `test_drawdown_warning`, `test_risk_policy`, `test_dashboard_ipc`, `test_ws_reconciliation`, `test_runtime_safety_regressions`, `test_daily_circuit_breaker` OK.
- `compileall main.py core tools/dashboard_api_server.py` OK.
- `ruff check core/ tests/ tools/dashboard_api_server.py` OK.
- `ruff format --check core/ tests/ tools/dashboard_api_server.py` OK.
- `scripts/smoke_modular_imports.sh` OK.
- `tools/regression_contracts.py` OK.
- `tools/check_no_silent_pass.py` OK.
- Suite unitaria completa: `1106` tests OK, `2` skipped.

### 2026-07-08 - Fase 3 institucional: model_version, watchdog timeout y runbook

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `core/bot_radar.py`: cada ronda de consenso ahora incluye `model_version` con `model_type`, `bootstrap_heuristic_mode` y `features_version`.
- `core/state_snapshot.py`: el snapshot expone `model_version` en cada entrada de consenso.
- `core/ws_reconciliation.py`: nuevo watchdog daemon `_check_ws_reconcile_timeout` que alerta si `ws_reconciliation_in_progress` stays active > `WS_RECONCILE_TIMEOUT_SECONDS` (default 30s). Envia evento `WS_RECONCILE_TIMEOUT_ALERT` y Telegram si esta configurado.
- `ops/runbook.md`: nuevo runbook operativo para PAPER/SHADOW con procedimientos ante HALT, integrity lock, WS reconcile prolongada, NEUTRAL_AGENT_VOTE frecuente y dashboard no responde.

Reglas preventivas:

- No cambiar `WS_RECONCILE_TIMEOUT_SECONDS` por debajo de 10s sin validar que el watchdog no dispare falsos positivos.
- No remover `model_version` del payload de consenso; es parte de la trazabilidad de auditoria.
- No crear un RiskManager paralelo; los gaps institucionales se cubren extendiendo `risk_policy` y `ws_reconciliation`.
- El runbook aplica solo a PAPER/SHADOW. Antes de operar REAL, debe completarse con procedimientos de recuperacion de capital.

Validacion registrada:

- Tests enfocados `test_ws_reconciliation`, `test_dashboard_ipc`, `test_risk_policy` OK.
- `compileall main.py core` OK.
- `ruff check core/ tests/` OK.
- `ruff format --check core/ tests/` OK.
- `scripts/smoke_modular_imports.sh` OK.
- `tools/regression_contracts.py` OK.
- `tools/check_no_silent_pass.py` OK.
- Suite unitaria completa: `1101` tests OK, `2` skipped.

### 2026-07-08 - Dashboard Votos / Consenso con endpoint canonico

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `core/bot_initialization.py`: agrega `consensus_history` como `deque(maxlen=200)` y `consensus_lock` separado de `scanner_history`.
- `core/bot_radar.py`: registra rondas compactas de consenso sin inflar el radar visual.
- `core/state_snapshot.py`: expone `consensus.latest`, `consensus.rounds`, `consensus.risk_summary` y `ws_reconciliation_in_progress` en el snapshot materializado.
- `tools/dashboard_api_server.py`: agrega `/api/v1/consensus?limit=50` leyendo solo el snapshot, no logs crudos.
- `dashboard/static/index.html`: agrega tab `Votos / Consenso` con gauge CSS, tabla de agentes, panel de risk gate y grafico historico Chart.js reutilizable.

Reglas preventivas:

- No usar `execution_events.jsonl` como fuente primaria de la UI de consenso; el endpoint canonico es `/api/v1/consensus`.
- No meter logica de riesgo en JavaScript; el frontend solo dibuja el payload ya procesado.
- No inflar `scanner_history`; mantener `consensus_history` separado, corto y en memoria.
- No recrear `Chart.js` en cada polling; actualizar datasets y usar `update('none')`.
- No anadir WebSocket UI salvo necesidad real; el dashboard sigue por HTTP polling.

Validacion registrada:

- `test_dashboard_ipc` y `test_risk_policy` OK.
- `compileall main.py core tools/dashboard_api_server.py` OK.
- `ruff check core/ tools/dashboard_api_server.py tests/test_dashboard_ipc.py` OK.
- `ruff format --check core/ tools/dashboard_api_server.py tests/test_dashboard_ipc.py` OK.
- `scripts/smoke_modular_imports.sh` OK.
- `tools/regression_contracts.py` OK.
- Suite unitaria completa: `1100` tests OK, `2` skipped.
- `git diff --check` OK.

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

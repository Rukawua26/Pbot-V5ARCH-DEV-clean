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

### 2026-07-13 - Fix: spread real alimenta RRR y veto experimental BULL_TREND

Trigger: analisis post-fix mostro profit factor ~0.64, 95%+ de perdidas con MAE <=0.5% y cero eventos `RISK_REWARD_VETO`. Auditoria externa pidio verificar signo SELL, unidades de spread y origen del spread real.

Diagnostico:
- El signo de SELL en `_evaluate_risk_reward_filter` era correcto: SELL penaliza `estimated_entry = entry * (1 - penalty)`.
- Las unidades eran correctas: `spread` se trata como fraccion y se aplica multiplicando el precio.
- Bug real: `core/market_intelligence.py` calculaba `spread` desde book ticker, pero no lo propagaba en el item `ranked`; `core/signals/context.py` recibia `ind.get("spread", 0.0)`, por lo que snapshots y RRR llegaban con `spread=0.0`.

Cambios:
- `core/market_intelligence.py`: cada entrada de triaje conserva `spread` calculado desde bid/ask.
- `core/bot_signals.py`: antes de construir contexto, copia `triage_entry["spread"]` a `ind["spread"]` cuando `Strategy.analyze` no lo trae.
- `core/config/manager.py` y `core/signals/filters.py`: nuevo `BULL_TREND_ENTRY_VETO_ENABLED` default `true`, con veto explicito `BULL_TREND_ENTRY_VETO` para pausar entradas en `BULL_TREND/BULL_STRONG` tras muestra reciente de 25 trades con 12% WR y avg -3.35%.
- Tests: `test_market_intelligence_and_balance`, `test_bot_signal_scan_cycle`, `test_min_atr_filter`, `test_regime_hmm`.

Reglas preventivas:
- El `spread` usado por RRR debe venir del book ticker reciente y viajar en el contexto de señal; no debe caer silenciosamente a `0.0` salvo ausencia real de dato.
- Si se agrega un filtro por regimen, debe tener flag de config y evento/log explicito para medirlo como experimento reversible.
- No considerar Sprint/RRR validado hasta observar eventos `RISK_REWARD_VETO` o confirmar en telemetria que el spread real llega a snapshots.

### 2026-07-10 - Housekeeping fail-safe y heartbeat PAPER testnet

Problema:
- Un import incorrecto de `reporter` en el reporte móvil periódico propagaba `ModuleNotFoundError` al loop principal. Como `last_report_time` no avanzaba, el bot reintentaba cada 10 segundos y no alcanzaba el escaneo de señales.
- El heartbeat llamaba `exchange.fetch_status()` en Binance testnet, que intenta un endpoint SAPI sin URL sandbox y marcaba la API offline aunque los endpoints Futures públicos funcionaran.

Cambios:
- `core/bot_housekeeping.py`: usa `tools.reporter`, contiene cualquier fallo de generación o encolado, aplica backoff exponencial de 60 a 3600 segundos y emite métricas `mobile_report`.
- `tools/notifier.py`: el encolado de mensajes devuelve `True/False` para que tareas opcionales puedan distinguir aceptación local de descarte o excepción.
- `core/config/operational.py`: agrega `AUTO_MOBILE_REPORTS_ENABLED` con default `True`.
- `core/bot_runtime_ops.py`: espera `init_complete`; en `PAPER + USE_TESTNET` prueba conectividad mediante `execution.fetch_ticker("BTC/USDT")` y conserva `fetch_status()` fuera de ese caso.
- `tests/test_low_coverage_ops.py`: cubre contención, backoff, recuperación, desactivación, reporte diario y separación del probe testnet.

Reglas preventivas:
- Tareas opcionales de housekeeping nunca deben propagar errores al loop de escaneo.
- Un reintento periódico fallido debe tener backoff y telemetría; no puede reiniciar el ciclo cada 10 segundos.
- En PAPER testnet no usar endpoints SAPI no soportados para determinar salud de Binance Futures.

Validación:
- Suite completa: 1132 tests OK, 2 skipped.
- `ruff check`, `ruff format --check`, `compileall`, smoke modular, contratos arquitectónicos y guard de `pass` silenciosos: OK.
- Reinicio PAPER observado: ciclo de 30 pares completado, operación SHADOW registrada y sin nuevos errores `No module named 'reporter'` ni fallos SAPI del heartbeat.

### 2026-07-09 - Plan de Reparacion de Edge — Fase 0 y 1 completadas

Documento completo: `docs/runbooks/plan-reparacion-edge.md`. Registrado en roadmap (#10).

Fase 0 (diagnostico base) — COMPLETADA:
- Config efectiva cargada congelada (ver snapshot en plan).
- Estado de DB: 49 trades SHADOW, winrate 26.5%, avg pnl -2.06%.
- 94.4% de perdidas con MFE<=0.5% (fallo de entrada, no de salida).
- confianza no calibrada (avg win 74.38 vs avg loss 74.41).
- Vetos dominantes recientes: COHERENCIA 692, MARKET_BREADTH_FEAR 318, SIDE_PARITY ~430, HMM_RANGE_PENALTY 125.

Fase 1 (bootstrap) — COMPLETADA:
- Confirmado: no existe ningun modelo ML en el workspace.
- `core/bot_models_startup.py:97` cae a `bootstrap_heuristic_mode=True` por ausencia de todos los archivos de modelo.
- En DB: `4062/4161` señales como `BOOTSTRAP_NONE`.
- `conflict_ab.log` (1222 conflictos) NO es evidencia valida de desalineacion ML vs reglas: `ml_pure_prob=0.0` cuando `bootstrap_heuristic_mode=True` (`core/bot_signals.py:242`). Es codigo de "no hay modelo", no "modelo en contra".
- `tools/train_models.py` requiere minimo 50 muestras; hay 49 trades y 33 snapshots. Dataset insuficiente.

Decision tomada con el usuario:
- Periodo de acumulacion en SHADOW hasta 100-150 trades.
- Para acumular data, relajar filtros de entrada que hoy bloquean todos los trades (Fase 2).
- No tocar estrategia ni score aun.
- No reentrenar ghost model hasta tener dataset suficiente (100-150 muestras).
- No pasar a REAL en este periodo.

### 2026-07-09 - Experimento 1: Ablacion de filtros de calidad (EN EJECUCION)

Plan aprobado: desnudar el sistema de filtros, aceptar entrada de trades basura y reconstruir capa por capa hasta encontrar el punto de quiebre. Documento completo: `docs/runbooks/plan-reparacion-edge.md` (Fase 7).

Cambio aplicado en `.env` (no en codigo):
- 11 filtros de calidad pausados: `DIRECTIONAL_COHERENCE_FILTER`, `SIDE_PARITY_FILTER_ENABLED`, `GLOBAL_FEAR_GREED_FILTER_ENABLED`, `GLOBAL_BTC_DOM_FILTER_ENABLED`, `HMM_RANGE_VETO`, `OI_FILTER_ENABLED`, `CVD_FILTER_ENABLED`, `MTF_FILTER_ENABLED`, `EMA_ALIGNMENT_FILTER_ENABLED`, `EMA_SLOPE_FILTER_ENABLED`, `BREAKOUT_WATCH_ENABLED` -> todos `false`.
- Seguridad runtime intacta: `HALT`, `INTEGRITY_LOCK`, cooldowns, limits, watchdog, SL/TP, reconciliacion.
- Parametros de umbral intactos: `SHADOW_MODE_MIN=55.0`, `REAL_MODE_THRESHOLD=70.0`, `MAX_ENTRY_SL_PCT=3.5`, `SHOCK_MIN_DIST_PCT=0.18`, `MIN_RISK_REWARD_RATIO=1.5`.
- Bootstrap 4/5 intacto (no relajado).
- `PAPER_MODE=true`, `EXECUTION_BACKEND=live`.

Veto embebido sin flag descubierto:
- `MARKET_BREADTH_FEAR` (`core/signals/filters.py:562`): veto siempre activo si el contexto reporta `FEAR`. No tiene flag de activacion. Si persiste tras ablacion, hay que investigarlo como capa embebida (Escenario E).

Fix de `.env` duplicado:
- Las lineas 86-87 del `.env` (seccion SHADOW VALIDATION CAMPAIGN) tenian `GLOBAL_FEAR_GREED_FILTER_ENABLED=true` y `GLOBAL_BTC_DOM_FILTER_ENABLED=true` que pisaban las nuevas lineas de ablacion porque aparecian despues. `load_dotenv` mantiene la ultima ocurrencia. Corregido poniendo ambas a `false` en ambas secciones.

Validacion:
- Flags efectivos confirmados: los 11 filtros cargan `False`.
- `tests/test_filters_execution_mode.py` + `tests/test_filters_pure_functions.py`: 62/62 OK.
- `scripts/smoke_modular_imports.sh`: OK.
- `compileall core config.py`: OK.

Reglas preventivas:
- No confiar en `conflict_ab.log` mientras estemos en bootstrap: sus `ML_CONFIDENCE 0.0%` son ruido de "sin modelo", no senal de decision.
- No reducir `--min-samples` de `tools/train_models.py` para forzar entrenamiento con 49 muestras: alto riesgo de overfit.
- No tocar `core/bot_models_startup.py`: su comportamiento de caer a bootstrap ante ausencia de modelo es correcto.
- No reintroducir varios filtros a la vez tras el Experimento 1: un cambio por experimento para aislar el diagnostico.
- No pasar a `REAL` hasta cumplir winrate SHADOW mejorado + buckets de confianza calibrados + 100-150 trades acumulados.

Que sigue:
- Correr ventana 24-48h en SHADOW.
- Extraer metricas con el checklist de lectura post-experimento del plan.
- Clasificar en Escenario A-F y decidir Experimento 2 (bootstrap relaxation) o reintroduccion de filtros.

### 2026-07-09 - Experimento 2: Relajar bootstrap SHADOW de 4/5 a 3/5 reglas (EN EJECUCION)

Trigger: Experimento 1 confirmo Escenario B. Radar mostro 13/23 señales en `BOOTSTRAP NO_FIRE` con 2-3 reglas. `BEAR_REVERSAL_VETO` pausado previamente (threshold 100.0).

Cambios aplicados:
- `core/config/manager.py`: añadido `BOOTSTRAP_SHADOW_MIN_HITS = _env_int("BOOTSTRAP_SHADOW_MIN_HITS", 4)` con default 4 (preserva comportamiento original).
- `core/signals/filters.py:235`: `bootstrap_ready_shadow` ahora usa `Config.BOOTSTRAP_SHADOW_MIN_HITS` en vez de hardcoded `4`.
- `.env`: `BOOTSTRAP_SHADOW_MIN_HITS=3`.
- `bootstrap_ready_real` sigue en `5/5` (REAL no se toca).

Reglas preventivas:
- No bajar `BOOTSTRAP_SHADOW_MIN_HITS` por debajo de 3 sin medir calidad de entradas.
- No cambiar `bootstrap_ready_real` de `5/5` para REAL: es la guarda de seguridad cuando no hay modelo ML.
- Reintroducir `BOOTSTRAP_SHADOW_MIN_HITS=4` solo tras acumular 100-150 trades y entrenar ghost model.

Validacion:
- `compileall core/signals/filters.py core/config/manager.py`: OK.
- `ruff check` + `ruff format --check`: OK.
- `tests/test_filters_execution_mode.py`: 21/21 OK.
- `BOOTSTRAP_SHADOW_MIN_HITS=3` confirmado cargado.

### 2026-07-09 - Experimento 3: Reactivar coherencia direccional (EN EJECUCION)

Trigger: baseline ya genera entrada/salida correctamente y acumulo >60 trades SHADOW. El siguiente paso del plan es reintroducir un filtro por vez.

Cambio aplicado:
- `.env`: `DIRECTIONAL_COHERENCE_FILTER=true`.

Mantener igual:
- `BOOTSTRAP_SHADOW_MIN_HITS=3`.
- `MARKOV_PREVETO_BEARISH_REVERSAL_MIN=100.0`.
- `SIDE_PARITY_FILTER_ENABLED=false` y resto de filtros de calidad pausados.

Objetivo:
- Medir si alinear trades con `current_sentiment` reduce entradas contra sesgo direccional sin secar el flujo.
- Con sentiment alcista, se espera bloquear la mayoria de `SELL` y dejar pasar `BUY`.

Regla preventiva:
- No reactivar otro filtro hasta cerrar ventana de observacion de Experimento 3.

### 2026-07-10 - Experimento 3: pausar `MARKET_BREADTH_FEAR` para aislar coherencia

Trigger: radar con `DIRECTIONAL_COHERENCE_FILTER=true` mostro que los SELL quedan bloqueados correctamente por coherencia, pero los BUY quedaron bloqueados masivamente por `MARKET_BREADTH_FEAR: FEAR (100% dump)`.

Cambios aplicados:
- `core/config/manager.py`: añadido `MARKET_BREADTH_FEAR_FILTER_ENABLED = _env_bool("MARKET_BREADTH_FEAR_FILTER_ENABLED", True)`.
- `core/signals/filters.py`: `MARKET_BREADTH_FEAR` ahora respeta `MARKET_BREADTH_FEAR_FILTER_ENABLED`.
- `.env`: `MARKET_BREADTH_FEAR_FILTER_ENABLED=false`.
- `tests/test_market_breadth.py`: cobertura para flag activo (veta) y flag apagado (permite).

Objetivo:
- Mantener `DIRECTIONAL_COHERENCE_FILTER=true` y eliminar el veto residual que impedia medir si los BUY pasan en sentimiento alcista.

Validacion:
- `tests.test_market_breadth` + `tests.test_filters_execution_mode`: 30/30 OK.
- `ruff check` OK.
- `ruff format --check` OK.

Regla preventiva:
- No reactivar `MARKET_BREADTH_FEAR_FILTER_ENABLED` hasta cerrar la observacion del Experimento 3 y decidir si aporta calidad o solo seca flujo.

### 2026-07-10 - Fase Torniquete: MIN_ATR_PCT, cap direccional SHADOW, HARD_SL ajustado

Trigger: Analisis de 183 trades SHADOW revelo que activos con ATR bajo (<0.5%) generan 93% de perdidas acumuladas, y clusters de hasta 13 BUY simultaneos amplifican perdidas correlacionadas.

Diagnostico corregido por asesor externo:
- MAE tracking FUNCIONA correctamente: 0.3% MAE en precio -> -5.1% PnL con 10x leverage + fees. No era bug, era interpretacion erronea mia.
- Confianza INVERTIDA: bucket [65-68] tiene 33.3% WR (+0.52% avg), bucket [80-90] tiene 15.0% WR (-2.42% avg). La heuristica bootstrap recompensa atributos no predictivos.
- ATR pct > 0.7% mejora WR de 23.6% a 25.9% y avg PnL de -1.81% a -1.65%. Umbral optimo: 0.6%.

Cambios:

1. MIN_ATR_PCT filter (core/config/manager.py, core/signals/filters.py, .env):
   - MIN_ATR_PCT=0.006 (0.6%). Veta simbolos con ATR ratio < threshold.
   - MIN_ATR_PCT_FILTER_ENABLED=true (env-overridable).
   - Umbral 0.6% bloquea ~11% de trades, mejora WR de 23.6% a 26.1%.
   - tests/test_min_atr_filter.py: 4 tests.

2. Cap direccional SHADOW (core/trade_entry.py, .env):
   - MAX_SHADOW_DIRECTIONAL_TRADES=3. Limita trades SHADOW por direccion.
   - Evita cluster 13 BUY simultaneos (causa raiz de drawdown masivo en dump).
   - tests/test_shadow_directional_cap.py: 4 tests.

3. HARD_SL mas ajustado (core/config/manager.py, .env):
   - SHADOW_HARD_SL_PERCENT=-3.5 (antes -5.0%).
   - REAL_HARD_SL_PERCENT=-3.0. Ambos env-overridable via manager.py.

Hallazgo arquitectonico no resuelto: el ATR SL (entry - ATR x 2.0) y el HARD_SL (-3.5%) estan desalineados con 10x leverage. El HARD_SL dispara a ~0.35% price move (3.5% PnL), muy antes del ATR SL a 1-2% price (10-20% PnL). El mecanismo ATR SL es irrelevante mientras el HARD_SL fijo sea mas restrictivo. Solucion futura: reducir STOP_LOSS_ATR_MODIFIER a ~0.6 o reducir leverage.

Reglas preventivas:
- No bajar MIN_ATR_PCT bajo 0.005 sin re-medir distribucion ATR winners vs losers.
- No subir MAX_SHADOW_DIRECTIONAL_TRADES sobre 5 sin medir correlacion en clusters.
- No subir SHADOW_HARD_SL_PERCENT sobre -3.0 sin validar alineacion con ATR SL.
- Si cambia leverage, recalibrar SHADOW_HARD_SL_PERCENT.

Que sigue:
- Encender bot, medir 50-100 trades SHADOW contra baseline.
- Evaluar si STOP_LOSS_ATR_MODIFIER necesita reduccion (Fase 3.1 profunda).

Reglas preventivas:

### 2026-07-11 - Fix: Hard SL priority over ExitEngineV1 (TIME_DECAY_ESCAPE_VELOCITY)

Trigger: Post-config (id>=188) mostro perdidas de -18.63% (GALA) y -9.71% (MON) cerradas como TIME_DECAY_ESCAPE_VELOCITY cuando HARD_SL era -3.5%. El Hard SL absoluto nunca disparaba porque el exit engine corria primero.

Causa raiz:
- En `core/bot_guardian.py`, `ExitEngineV1.evaluate_exit()` se ejecutaba en linea ~490, ANTES del chequeo `if t["pnl"] <= max_loss` en linea ~699.
- `check_time_decay_exit()` en `core/risk/exit_engine_v1.py` no tenia PnL floor: cerraba por time decay sin importar si el PnL ya superaba el Hard SL.
- `check_flat_volatility_exit()` tenia el mismo problema.
- Resultado: trades con PnL -18% se cerraban como TIME_DECAY_ESCAPE_VELOCITY en vez de Hard SL (-3.5%).

Cambios:

1. Reordenar Hard SL check en guardian (`core/bot_guardian.py`):
   - Mover `if t["pnl"] <= max_loss: bot.close_trade(...)` a justo despues del calculo de PnL (linea ~480), ANTES del exit engine.
   - Eliminar el chequeo duplicado de max_loss en la seccion posterior.
   - El PRE-SL WARNING ahora reutiliza max_loss ya calculado.

2. PnL floor en exit engine (`core/risk/exit_engine_v1.py`):
   - `check_time_decay_exit()`: retorna None si `pnl_pct <= hard_sl_percent`. Defense-in-depth.
   - `check_flat_volatility_exit()`: mismo guard. No cerrar por flat vol si PnL ya paso Hard SL.

3. Reactivar HMM_RANGE_VETO (`.env`):
   - `HMM_RANGE_VETO=true` (antes false). Bloquea BUY en regimen RANGE/BEAR_TREND.
   - Post-config mostro 21/29 trades BUY en BEAR_TREND/RANGE con HMM_RANGE_PENALTY.

4. Tests (`tests/test_hard_sl_priority.py`): 6 tests de regresion.
   - time_decay no dispara cuando PnL <= SHADOW_HARD_SL_PERCENT.
   - time_decay no dispara cuando PnL <= REAL_HARD_SL_PERCENT.
   - time_decay si dispara cuando PnL > Hard SL (comportamiento normal).
   - flat_volatility no dispara cuando PnL <= Hard SL.
   - flat_volatility si dispara cuando PnL > Hard SL.
   - evaluate_exit() completo no retorna TIME_DECAY cuando PnL past Hard SL.

Reglas preventivas:
- El Hard SL absoluto debe ser SIEMPRE el primer check despues de calcular PnL, antes que cualquier exit engine, trailing, o price SL.
- No introducir checks de salida antes del Hard SL en el guardian loop.
- check_time_decay_exit y check_flat_volatility_exit deben respetar el PnL floor del Hard SL.
- Si se cambia SHADOW_HARD_SL_PERCENT, verificar que el exit engine respete el nuevo valor.

Reglas preventivas:
- No confiar en `conflict_ab.log` mientras estemos en bootstrap: sus `ML_CONFIDENCE 0.0%` son ruido de "sin modelo", no senal de decision.
- No reducir `--min-samples` de `tools/train_models.py` para forzar entrenamiento con 49 muestras: alto riesgo de overfit.
- No tocar `core/bot_models_startup.py`: su comportamiento de caer a bootstrap ante ausencia de modelo es correcto.

Que sigue (Fase 2):
- Rankear filtros activos que bloquean entrada SHADOW hoy.
- Relajar solo los que vetan mucho sin mejorar winrate de los que pasan.
- Un cambio por experimento, ventana de 48-72h en SHADOW.

### 2026-07-09 - Fix: cierre REAL fallido activa HALT (fail-safe)

Commit: pendiente hasta cerrar este cambio.

Que cambia:

- `core/trade_exit.py`: en el bloque `except` de `close_trade` para cierres REALES, `close_failed` ahora se inicializa en `True` (fail-safe) en lugar de `False`. Solo se revierte a `False` cuando la orden reporta filled Y el exchange confirma posición plana via `_exchange_position_is_flat`.
- `tests/test_trade_exit.py`: agrega 3 tests de regresion:
  - `test_unclassified_exception_without_filled_order_halts`: excepcion generica + order no filled debe HALT.
  - `test_unclassified_exception_with_filled_and_flat_does_not_halt`: orden filled + posicion plana NO debe HALT (cierre valido).
  - `test_unclassified_exception_filled_but_not_flat_halts`: orden filled pero posicion sigue viva debe HALT.

Reglas preventivas:

- En el bloque `except` de cierre REAL, nunca inicializar `close_failed = False`. Asumir fallo y solo descartar si el exchange confirma posicion plana.
- Respetar el invariante "ante estado live ambiguo, preferir HALT y reconciliacion antes de continuar".
- No atajar excepciones genericas sin clasificar y dejar la posicion viva sin HALT; duplica riesgo de exposicion.

Validacion registrada:

- `tests/test_trade_exit.py` OK (28/28).
- `ruff check` y `ruff format --check` OK.
- `compileall core/trade_exit.py tests/test_trade_exit.py` OK.
- `tools/regression_contracts.py` OK.
- `tools/check_no_silent_pass.py` OK.
- `scripts/smoke_modular_imports.sh` OK.
- `tests/test_runtime_safety_regressions.py` OK.

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

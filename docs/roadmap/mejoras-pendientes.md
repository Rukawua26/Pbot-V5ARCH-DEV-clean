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
- **SHADOW Validation Campaign** implementada como telemetria observacional (`SHADOW_VALIDATION_ENABLED`) y reporte `tools/shadow_validation_report.py`.

## Mejoras Pendientes

### 1. FVG Tracker — Medicion estadistica en PAPER/SHADOW

FVG Tracker ya implementado. Pendiente:

1. Activar `FVG_TRACKER_ENABLED=true` en PAPER o SHADOW.
2. Activar `SHADOW_VALIDATION_ENABLED=true` para registrar ciclos FVG y correlacionarlos con trades SHADOW.
3. Medir si mejora MAE/MFE, winrate o reduce entradas malas.
4. Si solo genera ruido, mantener como herramienta observacional.

Criterio de exito: informacion incremental medible sobre trades existentes. Sin evidencia, no integrar al Risk Engine ni a ejecucion.

### 2. Global Market Provider (CoinGecko REST) — IMPLEMENTADO

Satelite read-only en `core/providers/global_market.py`. Inyecta 7 campos macro en `ctx`:
- `btc_dominance`, `eth_dominance`, `total_market_cap`, `total_volume_24h`
- `fear_greed_index`, `active_cryptos`, `trending_coins`

Flags: `GLOBAL_MARKET_PROVIDER_ENABLED`, `GLOBAL_MARKET_CACHE_TTL`, `GLOBAL_MARKET_USE_MCP`.

Pendiente:
1. Activar `GLOBAL_MARKET_PROVIDER_ENABLED=true` en PAPER o SHADOW.
2. Revisar calidad de datos: CoinGecko gratis tiene rate limit, validar que no haya huecos.
3. Si se necesita MCP, implementar `_fetch_from_mcp()` en el provider.
4. Usar `tools/shadow_validation_report.py` para medir vetos/boosts macro antes de tocar thresholds.

### 3. Filtros Macro-Reactivos — IMPLEMENTADO

Veto/boost en `core/signals/filters.py` basado en Fear & Greed y BTC dominance.
Flags: `GLOBAL_FEAR_GREED_FILTER_ENABLED`, `GLOBAL_BTC_DOM_FILTER_ENABLED`,
`GLOBAL_FEAR_VETO_THRESHOLD`, `GLOBAL_BTC_DOM_BOOST_THRESHOLD`.

Pendiente:
1. Validar en PAPER/SHADOW que los thresholds actuales (fear<20 veto, dom>65% boost) sean óptimos.
2. Añadir más reglas: total_market_cap drop % veto, eth_dominance altseason boost.
3. No ajustar thresholds hasta tener 20+ trades SHADOW cerrados en el reporte de validacion.

### 4. Auto-Replication de Estrategias Ganadoras — PENDIENTE (Futuro)

Cuando el RAG detecte que las condiciones actuales tienen ≥90% de similitud con 3+ trades ganadores,
ejecutar automáticamente la señal en SHADOW (sin esperar consenso ML completo).

Estado: NO implementado. Requiere datos suficientes en `trade_context_snapshots` primero.

Pasos:
1. Recolectar datos SHADOW con Fase 1 y 3 activas por al menos 1 semana.
2. Validar que los vectores de similitud con macro (btc_dominance, fear_greed) mejoran la correlación.
3. Implementar bloque en `core/trade_entry.py` post-similarity-search.
4. Restringir a SHADOW inicialmente (`REPLICATION_MODE=shadow`).
5. Flags: `REPLICATION_ENABLED`, `REPLICATION_MIN_WINNERS`, `REPLICATION_MIN_SIMILARITY`.

Criterio de exito: winrate > 65% en trades replicados vs ~50% baseline, con al menos 20 muestras.

### 5. Dashboard API — SNIPER_API_KEY requerida

`tools/dashboard_api_server.py` requiere `SNIPER_API_KEY` con al menos 16 caracteres para iniciar.
Si no esta configurada, el dashboard API lanza warning pero el bot sigue operando normal.
El dashboard localhost usa cookie HttpOnly para lectura automatica sin prompt del navegador.

Pendiente:
1. Definir `SNIPER_API_KEY` segura en `.env` si se va a usar el dashboard.
2. Si el dashboard no se usa, evaluar flag para no iniciar el API y silenciar el warning.
3. Documentar la variable en `.env.example` si aplica.

Criterio de exito: bot arranca sin warning cuando dashboard esta habilitado, o dashboard queda apagado explicitamente cuando no se use.

### 5.1 Dashboard Votos / Consenso — PENDIENTE

Objetivo: ver desde `http://127.0.0.1:8000` los votos MT/SR/G, consenso, score direccional, override y razon exacta de veto por simbolo.

Pendiente:
1. Ampliar `core/state_snapshot.py` o agregar endpoint read-only `/api/v1/signals/live`.
2. Exponer por simbolo: `votos`, `agent_direction_score`, `agent_signal_override`, `audit_verdict`, `filter_reason`, `prob_final`.
3. Agregar pestana UI "Votos / Consenso" en `dashboard/static/index.html`.
4. Mantener solo lectura en esta fase; no permitir force entry, override o cambios de pesos hasta terminar la campana SHADOW.

Criterio de exito: el usuario puede auditar por localhost por que una senal 70-80% fue vetada sin consultar logs ni consola.

### 6. Direccion por Consenso de Agentes + Trailing Adaptativo — IMPLEMENTADO

Cambios aplicados:
1. `tools/strategy.py`: `_resolve_signal_from_agents()` permite que MT/SR/G reviertan la direccion EMA cuando hay consenso fuerte.
2. `core/strategy/orchestrator.py`: `calculate_consensus()` devuelve `final_weights` para resolver direccion ponderada.
3. `core/bot_guardian.py`: trailing adaptativo por regimen, mas permisivo en `RANGE`.
4. `core/config/strategy.py`: trailing menos agresivo (`TRAILING_ACTIVATION_PNL=1.20`, `TRAILING_BREAKEVEN_PNL=3.0`, `TRAILING_BREAKEVEN_PULLBACK=2.0`).
5. `core/config/manager.py`: flags `SIGNAL_AGENT_OVERRIDE_ENABLED`, `SIGNAL_AGENT_OVERRIDE_THRESHOLD`, `EXIT_RANGE_BREAKEVEN_PULLBACK_MULT`, `EXIT_RANGE_ACTIVATION_MULT`.

Pendiente:
1. Recolectar al menos 10 trades SHADOW cerrados post-cambio.
2. Comparar contra baseline previo: 17 SHADOW trades, 35.3% WR, PnL total -12.17%.
3. Medir si aparecen mas BUY utiles sin degradar proteccion macro BTC.
4. Ajustar `SIGNAL_AGENT_OVERRIDE_THRESHOLD` si los agentes revierten demasiado o demasiado poco.
5. Ajustar multiplicadores de trailing si las ganadoras siguen cerrando temprano.
6. Usar `SHADOW_VALIDATION_ENABLED=true` para medir `agent_override_rate_pct`, WR y avg win/loss.

Criterio de exito: winrate SHADOW >45% y mejor relacion avg win/avg loss sin aumentar drawdown ni saltarse filtros macro.

### 7. Plan de Optimizacion Cuantitativa del Bot — PENDIENTE

Objetivo: mejorar calidad matematica, eficiencia del pipeline y estabilidad del aprendizaje antes de escalar la campana SHADOW o pensar en REAL.

Restricciones:

1. No tocar `cmd_consumer.py`, IPC del dashboard, `ws_reconciliation.py`, `scanner_history` ni `consensus_history` salvo bug real.
2. No cambiar `EMA_SLOPE_LOOKBACK` por intuicion; medir primero.
3. No introducir Redis/CQRS en esta fase.
4. Cada veto nuevo debe quedar visible en logs estructurados, dashboard/radar y tests.
5. Mantener cambios pequenos, medibles y reversibles.

#### Sprint 1 — Seguridad matematica: filtro RRR estructural — IMPLEMENTADO

Problema corregido: una senal con consenso alto podia llegar a entrada aunque el `TP/SL` tuviera mala esperanza matematica.

Implementado:

1. Filtro de ratio riesgo/beneficio minimo despues de calcular `sl_val` y `tp_val`, antes del sizing/ejecucion en `core/trade_entry.py`.
2. RRR con precio estimado defensivo ante spread/slippage:
    - BUY: penalizar entrada hacia arriba.
    - SELL: penalizar entrada hacia abajo.
3. Uso de `spread`, `MAX_SLIPPAGE` y `atr_pct` para evitar aceptar trades al filo del umbral.
4. Configuracion:
    - `RISK_REWARD_FILTER_ENABLED=true`
    - `MIN_RISK_REWARD_RATIO=1.5`
    - `RISK_REWARD_VOLATILITY_BOOST_ENABLED=true`
    - `RISK_REWARD_HIGH_VOL_MIN_RATIO=1.7`
5. Evento estructurado `RISK_REWARD_VETO` con `symbol`, `side`, `entry_price`, `estimated_entry`, `sl_val`, `tp_val`, `risk`, `reward`, `actual_rrr`, `required_rrr`, `spread` y `atr_pct`.
6. Veto visible en dashboard/radar como `RRR ESTRUCTURAL INSUFICIENTE`.
7. Tests agregados:
    - BUY con RRR valido pasa.
    - BUY con RRR invalido bloquea.
    - SELL con RRR valido pasa.
    - SELL con RRR invalido bloquea.
   - Bounds invalidos bloquean.
   - Penalizacion por spread/slippage reduce RRR.

Criterio de salida:

- Logs/eventos `RISK_REWARD_VETO` visibles y correctos.
- Tests nuevos en verde.
- No romper PAPER/SHADOW.

Pendiente operacional:

1. Medir durante campana PAPER/SHADOW si los trades aceptados mantienen RRR medio superior al minimo teorico.
2. Ajustar `MIN_RISK_REWARD_RATIO` o `RISK_REWARD_HIGH_VOL_MIN_RATIO` solo con evidencia de muestra cerrada.

#### Sprint 2 — Eficiencia del pipeline: pre-filtros baratos — IMPLEMENTADO

Problema corregido: parte del analisis pesado podia ejecutarse antes de descartar simbolos por reglas simples.

Implementado:

1. Helper `_passes_cheap_pre_filters(...)` antes de `_analyze_symbol_candidate(...)` en `core/bot_signals.py`.
2. Se usa tanto en `_precompute_signal_analysis(...)` como en `run_signal_scan_cycle(...)` para evitar analisis paralelo o secuencial innecesario.
3. Solo usa datos O(1) en RAM:
    - cooldown.
    - simbolo ya activo.
    - latency quarantine.
    - runtime symbol controls cacheados.
    - `res_data` vacio, `NO_DATA`, timeout o latencia extrema ya conocida.
4. No se movio a pre-filtro barato:
    - RSI contextual.
    - ADX contextual.
    - shock distance.
    - coherencia final.
    - MTF/OI.
    - filtros que dependan de `ctx` profundo.
5. Evento `CHEAP_PREFILTER_VETO` con razon (`COOLDOWN_ACTIVE`, `SYMBOL_ALREADY_ACTIVE`, `LATENCY_QUARANTINED`, `DATA_INTEGRITY_FAIL`, `SYMBOL_BLOCKED`).
6. Tests enfocados validan que simbolos bloqueados/activos/latencia extrema no llaman a `_analyze_symbol_candidate(...)`.

Criterio de salida:

- Menos invocaciones al analisis pesado.
- Vetos baratos visibles antes del consenso pesado.
- Tests nuevos en verde.

Pendiente operacional:

1. Medir `cycle_latency_ms` o tiempo por simbolo para comparar antes/despues durante PAPER/SHADOW.
2. Confirmar menor latencia media por ciclo y ausencia de cambios raros en senales validas.

#### Sprint 3 — Aprendizaje estable: genetica en batch — IMPLEMENTADO

Problema corregido: `evolve_genetics(symbol)` corria en el cierre individual y podia sobreajustar por ruido reciente.

Implementado:

1. `bot.brain.evolve_genetics(symbol)` sale del hot-path de `core/trade_exit.py`; el cierre solo encola el simbolo en `_genetic_batch_pending_symbols`.
2. Se mantiene por trade:
    - `update_trade_context_result(...)`
    - `finalize_confidence_exit_audit(...)`
    - `update_agent_reputation(...)`
3. Batch genetico en `core/bot_maintenance.py` con flags:
    - `GENETIC_BATCH_ENABLED=true`
    - `GENETIC_BATCH_MIN_TRADES=50`
4. El batch procesa solo simbolos pendientes con muestras suficientes y conserva pendientes sin muestras minimas.
5. Registrar eventos:
    - `GENETIC_BATCH_STARTED`
    - `GENETIC_BATCH_COMPLETED`
    - `GENETIC_BATCH_SKIPPED`
    - `GENETIC_BATCH_SWAP_APPLIED`
    - `GENETIC_BATCH_QUEUED`
6. Tests enfocados validan cierre sin evolucion inmediata, batch con muestras suficientes, batch insuficiente y batch deshabilitado.

Criterio de salida:

- Genetica fuera del cierre inmediato.
- Tests nuevos en verde.

Pendiente operacional:

1. Observar en PAPER/SHADOW que el batch no congela main loop ni WebSocket.
2. Si se refactoriza `Brain.evolve_genetics`, calcular parametros en copia aislada y aplicar con swap atomico corto.

#### Sprint 4 — Escala y sensibilidad — IMPLEMENTADO

Objetivo implementado: ampliar universo operable y medir sensibilidad sin adivinar.

Implementado:

1. Triage gradual despues de Sprints 1-3:
    - `TRIAGE_CANDIDATE_POOL_MULTIPLIER=2`
    - `TRIAGE_MAX_CANDIDATE_POOL=60`
2. Telemetria comparativa para `EMA_SLOPE_LOOKBACK`:
    - `EMA_SLOPE_LOOKBACK=2` se mantiene en ejecucion.
    - `EMA_SLOPE_COMPARISON_LOOKBACK=4` se calcula pasivamente como `ema50_slope_alt`.
    - `ema50_slope_alt_lookback` queda en snapshot/contexto para analisis posterior.

Pendiente operacional:

1. Medir durante 48h en SHADOW:
    - timeouts.
    - latencia del ciclo.
    - candidatos utiles.
    - ratio de vetos posteriores.
    - senales SHADOW seleccionadas.
2. No alterar ejecucion de slope hasta tener evidencia.

Criterio de salida:

- Triage ampliado con preset `2/60`.
- Datos pasivos suficientes para decidir si conviene ajustar slope.
- No se cambio sensibilidad por intuicion.

Orden de ejecucion obligatorio:

1. Sprint 1: RRR minimo spread/slippage-aware. IMPLEMENTADO.
2. Sprint 2: pre-filtros baratos solo en RAM. IMPLEMENTADO.
3. Sprint 3: genetica batch. IMPLEMENTADO.
4. Sprint 4: triage 2x + telemetria slope comparativa. IMPLEMENTADO.

### 8. Dashboard Votos / Consenso — Mejora visual y explicabilidad

Estado: implementado localmente, pendiente de commit/cierre.

Objetivo: que la pestana `Votos / Consenso` explique de forma inmediata que quiso hacer el bot, por que entro/no entro, que regimen favorecia y que modelo estaba activo.

Cambios implementados localmente:

1. Panel principal de respuesta humana:
   - `Señal seleccionada`
   - `Señal bloqueada`
   - `Bloqueado por coherencia`
   - `Sistema neutral`
2. Explicacion directa del motivo, por ejemplo:
   - `Señal BUY contra régimen BAJISTA. Dirección favorecida: SELL.`
3. KPIs visuales:
   - `Señal`
   - `Régimen`
   - `Favorece`
   - `Resultado`
4. Chips de auditoria del modelo:
   - `Model`
   - `Features`
   - `ML active` / `Heuristic`
5. Filtros del grafico de consenso:
   - `Todas`
   - `Bloqueadas`
   - `Seleccionadas`
   - `Neutras`
6. Colores semanticos en el grafico historico:
   - verde para seleccionadas
   - rojo para bloqueadas
   - azul para neutrales
   - ambar para observadas

Archivos tocados:

- `dashboard/static/index.html`
- `tests/test_dashboard_ipc.py`

Validacion local ya ejecutada:

- `tests/test_dashboard_ipc.py` OK.
- `ruff check tests/test_dashboard_ipc.py` OK.
- `ruff format --check tests/test_dashboard_ipc.py` OK.
- `git diff --check` OK.

Pendiente opcional:

1. Commit de la mejora visual junto con este roadmap.
2. Captura visual post-cambio para comparar antes/despues.
3. Si el usuario lo desea, agregar tooltip detallado por punto del grafico con `symbol`, `side`, `status`, `reason` y `prob_final`.

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

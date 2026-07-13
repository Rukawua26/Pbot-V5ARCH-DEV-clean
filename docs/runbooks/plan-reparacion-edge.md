# Plan de Reparacion de Edge — Bot de Trading

> Plan operativo para descubrir y reparar por que el bot es selectivo en volumen pero poco efectivo.
> Diagnostico inicial: el bot parece "selectivo" en cantidad, pero esa selectividad no esta filtrando edge real. Esta filtrando por restricciones, no por calidad predictiva.

## Diagnostico Base (Fase 0 confirmada)

### Evidencia inicial
- Base historica (`tools/sniper_brain.db`): 49 trades SHADOW, 13 wins, 36 losses, avg pnl -2.06%, winrate 26.5%.
- Confianza no calibrada: avg win conf 74.38 vs avg loss conf 74.41; bucket 80% tiene 9.1% winrate.
- 94.4% de las perdidas tuvieron MFE <= 0.5% (entradas malas, no salidas que arruinan trades buenos).
- 25/49 trades cierran por `Hard SL (-5.0%)`, winrate 0%; `Trailing (Breakeven Protection)` 10/10, winrate 100%.
- 48/49 trades en regimen RANGE, avg pnl -1.99%.
- `conflict_ab.log`: 1222 conflictos donde "ML queria abortar" (0% o 40%) pero reglas aprobaron operar.
- Config vieja mas estricta: KAVA 1.2%, SHOCK 0.4% en logs/DB; config actual: KAVA 3.5%, SHOCK 0.18%.

### Causas raiz probables (4 frentes)
1. **No hay modelo ML cargo** — gran parte del flujo reciente esta en `bootstrap_heuristic_mode`.
2. **Sobre-filtrado** — `COHERENCIA`, `SIDE_PARITY`, `MARKET_BREADTH_FEAR`, `HMM_RANGE_PENALTY` vetan masivas.
3. **Score mal calibrado** — `prob_final` no separa ganadores de perdedores.
4. **Sin edge en RANGE** — casi todo el historial cae en este regimen y ahi no hay ventaja.

### Causa exacta confirmada: confianza bootstrap invertida

Estado: confirmado durante Fase 0/Fase Torniquete. No es una desviacion del plan; es la explicacion mecanica del punto 3 (`Score mal calibrado`).

El bot no esta usando una probabilidad aprendida mientras corre en `bootstrap_heuristic_mode`. La confianza se calcula en `core/signals/filters.py` como:

```python
heuristic_confidence = min(90.0, 48.0 + (hit_count * 8.0))
```

Cada `hit` suma el mismo peso (+8), aunque no todos tienen valor predictivo real:

| Hit | Condicion BUY | Riesgo detectado |
| :--- | :--- | :--- |
| `EMA_ALIGN` | `close >= ema` | compra fuerza ya extendida. |
| `ADX_OK` | `adx >= 18` | confirma tendencia ya desarrollada, no necesariamente entrada temprana. |
| `RSI_OK` | `52 <= rsi <= 68` | compra momentum; en regimen lateral/bajista puede ser techo local. |
| `VOL_OK` | `vol_rel >= 1.05` | puede confirmar euforia tardia. |
| `ATR_OK` | `0 < atr_pct <= 0.05` | premia volatilidad contenida, pero el dato posterior mostro mejor WR con ATR alto (`atr_pct > 0.007`). |

Evidencia posterior:
- Bucket bajo/intermedio rinde mejor que bucket alto: `[65-68]` tuvo 33.3% WR y `80-90` tuvo 15.0% WR en el analisis ampliado.
- En los ultimos 20 trades post-fix, `65-70` tuvo 75.0% WR y +2.41% avg, mientras `70-75` tuvo 12.5% WR y -2.74% avg.
- La confianza alta no mide probabilidad de exito; mide cantidad de confluencias trend-following. En mercado lateral/mean-reverting, mas confluencias suele significar entrada mas tardia.

Implicacion para Fase 1:
- No entrenar ni evaluar el modelo usando `heuristic_confidence` como verdad objetivo.
- `prob_final` bootstrap debe tratarse como feature sospechosa o auxiliar, no como label ni probabilidad calibrada.
- La Fase 1 debe validar si un modelo aprendido corrige la inversion por bucket antes de permitir uso en REAL.
- Si el dataset sigue siendo pequeno, preferir recalibracion/ablation de hits antes que forzar entrenamiento con pocas muestras.

## Reglas De Trabajo

- Un cambio por experimento.
- Misma ventana temporal y mismo modo `SHADOW` para comparar.
- No mezclar datos de config vieja con config nueva.
- No pasar a `REAL` hasta que SHADOW muestre edge.
- Medir 48-72h entre cambios.
- Documentar cada fase antes de pasar a la siguiente.

## Fase 0 — Congelar Diagnostico Base

**Meta**: tener foto "antes de cambios".

### Pasos
- [x] Confirmar modo de trabajo (`PAPER_MODE`, `EXECUTION_BACKEND`).
- [x] Confirmar `bootstrap_heuristic_mode` y ausencia de modelo ghost.
- [x] Sacar metricas base de `tools/sniper_brain.db`.
- [x] Separar historico viejo vs reciente.
- [x] Confirmar configuracion efectiva cargada hoy.
- [x] Rankear vetos reales en `signal_alerts`.
- [x] Confirmar que MFE bajo en perdidas indica entrada mala.
- [x] Guardar este documento.

**Resultado**: foto base congelada. Ver seccion "Snapshot Fase 0" abajo.

## Fase 1 — Salir de Bootstrap

**Meta**: confirmar si el problema principal es ausencia de modelo.

**Insumo desde Fase 0**: la ausencia de modelo ya no es solo falta de ML; deja activa una heuristica bootstrap invertida. La Fase 1 debe comprobar que el modelo aprendido mejora la calibracion por buckets y no replica el sesgo de comprar fuerza tardia.

### Estado
- Confirmado: no existe ningun modelo en el workspace (`.pkl`/`.h5`).
- No existe el directorio `models/`.
- `core/bot_models_startup.py:97` cae a `bootstrap_heuristic_mode=True`.
- En DB: `4062/4161` señales como `BOOTSTRAP_NONE`.
- `tools/train_models.py` requiere minimo 50 muestras; hay 49 trades SHADOW y 33 snapshots. No se puede entrenar todavia.

### Decision
- Periodo de acumulacion en SHADOW hasta 100-150 trades.
- Necesario: relajar filtros de entrada para permitir more trades SHADOW (solo lo suficiente para acumular data).
- No tocar estrategia ni score aun.

### Pasos pendientes (Fase 1 cerrada -> mover a Fase 2/6 operativas)
- [x] Confirmar `core/bot_models_startup.py` y ausencia de modelos.
- [x] Verificar dataset insuficiente (49 < 50 minimo).
- [x] Decidir: periodo de acumulacion SHADOW + relajar filtros de entrada.
- [x] Al entrenar, excluir `heuristic_confidence` como label objetivo y auditar si conviene usarlo solo como feature auxiliar.
- [x] Validar calibracion post-modelo: buckets altos deben ganar mas que buckets bajos en out-of-sample.
- [ ] Comparar modelo vs bootstrap por regimen (`RANGE`, `BEAR_TREND`, `BULL_TREND`) y por side cuando el modelo supere gates globales.
- [ ] Relajar filtros que bloquean entrada hoy (Fase 2).
- [ ] Corregir telemetria de `conflict_ab.log` para no contaminar con `ml_pure_prob=0` de bootstrap.
- [x] Reentrenar ghost model cuando dataset alcance 100-150 muestras.

### Ejecucion 2026-07-13 — Ghost Model rechazado por OOS

Se ejecuto Fase 1 con dataset curado, sin activar runtime.

Curacion aplicada en `tools/train_models.py` / vista `vw_training_dataset`:
- Solo `is_shadow=1`.
- Excluir `is_dirty` e `is_adopted`.
- Requerir `market_snapshot` valido y `pnl_percent` real.
- Excluir ruido `ABS(pnl_percent) < 0.10`.
- Excluir slippage/extremos `ABS(pnl_percent) > 10.0`.
- Excluir `exit_reason='UNKNOWN'`.
- Entrenar Ghost solo con features disponibles en runtime: `rsi`, `adx`, `vol_rel`, `atr_pct`, `funding_rate`, `btc_delta_tf`.

Resultado del entrenamiento offline (`--ghost-only --no-legacy-copy`):
- Dataset curado: 279 trades.
- Train cronologico: 195 trades.
- OOS bloqueado: 84 trades (69 losses, 15 wins).
- Bootstrap OOS: AP=0.2200, Brier=0.4806, F1=0.3030.
- Ghost OOS: AP=0.2395, Brier=0.2056, F1=0.2581.
- Gates: AP > prevalencia OK, AP > bootstrap OK, Brier mejora >10% OK, F1 >= 0.30 FAIL.
- Decision: **modelo descartado, no se publico artefacto**. Runtime permanece en bootstrap.

Implicacion:
- Fase 1 confirma que ya hay data suficiente para intentar entrenamiento, pero no hay evidencia ciega suficiente para reemplazar bootstrap.
- No activar `REQUIRE_GHOST_MODEL_FOR_TRADING=True` ni copiar modelos hasta que Ghost supere todos los gates OOS.
- Siguiente paso alineado al plan: avanzar Fase 2/Fase 3 para mejorar calidad de entradas y reducir `HARD_SL` antes de reintentar entrenamiento con 500-1000 trades o mejores features.

### Ejecucion 2026-07-13 — Fase 2 quick fix: RRR con spread real + BULL_TREND veto

Motivo: revision externa de Sprint RRR detecto que no bastaba con tener `_evaluate_risk_reward_filter`; habia que verificar que el `spread` usado fuera real. En DB los snapshots recientes mostraban `spread=0.0` y no habia eventos `RISK_REWARD_VETO`.

Resultado:
- `market_intelligence` ahora conserva `spread` calculado desde book ticker en cada item de triaje.
- `bot_signals` propaga ese `spread` al `ind/context` antes de construir el snapshot y antes de ejecutar RRR.
- `BULL_TREND_ENTRY_VETO_ENABLED=true` por default bloquea entradas en `BULL_TREND/BULL_STRONG` como experimento reversible, dado el rendimiento reciente negativo del regimen.

Criterio de observacion post-reinicio:
- Nuevos snapshots deben mostrar `spread > 0` cuando Binance entregue bid/ask.
- Deben empezar a aparecer eventos/logs `RISK_REWARD_VETO` si el RRR efectivo cae bajo umbral.
- Medir 50-100 trades: objetivo inmediato `HARD_SL < 60%`, PF > 0.8 y desaparicion de trades en BULL_TREND.

### Ejecucion 2026-07-13 — Fase 2: RANGE hard veto

Motivo: despues del quick fix anterior hubo 11 trades nuevos, todos `SELL` en `RANGE`, con 0% WR y 10 `HARD_SL`. El `spread` real ya aparecia en snapshots, por lo que la ruta de perdida dominante era `HMM_RANGE_PENALTY` permitiendo aprendizaje en `RANGE`.

Cambio:
- `HMM_RANGE_LEARNING_OVERRIDE_ENABLED=false` por default.
- `allow_range_learning` ya no se activa solo por estar en PAPER/SHADOW; requiere override explicito.
- `HMM_RANGE_VETO=true` ahora produce `RANGE REGIME VETO` duro tambien en SHADOW.

Criterio de observacion post-reinicio:
- 0 trades nuevos con `market_regime='RANGE'`.
- Si el flujo cae demasiado, reabrir de forma controlada con `HMM_RANGE_LEARNING_OVERRIDE_ENABLED=true` o con condiciones adicionales, no con override implicito.
- Mantener medicion de 50-100 trades antes de reentrenar Ghost.

## Fase 2 — Rankear Filtros

**Meta**: descubrir que filtro hoy corta mas trades.

### Pasos
- [ ] Rankear vetos por frecuencia en `signal_alerts` (ya iniciado en Fase 0).
- [ ] Separar vetos por ventana temporal (viejo vs reciente vs ultimo dia).
- [ ] Calcular "veto rate" por filtro: `%` de señales descartadas por cada uno.
- [ ] Identificar filtros que vetan mucho pero no mejoran winrate de los que pasan.

### Filtros a revisar (prioridad)
1. `DIRECTIONAL_COHERENCE_FILTER` (592 vetos BUY en bajista + 100 SELL en alcista)
2. `GLOBAL_FEAR_GREED_FILTER_ENABLED` / `MARKET_BREADTH_FEAR` (318 vetos)
3. `SIDE_PARITY_FILTER_ENABLED` (181 + rama volumen/ADX)
4. `HMM_RANGE_VETO` / `HMM_RANGE_PENALTY` (125 vetos)
5. `SHOCK_MIN_DIST_PCT`
6. `MAX_ENTRY_SL_PCT` / `VETO_KAVA`
7. `RISK_REWARD_VETO`

### Criterio de exito
- Saber cual filtro hoy esta cortando mas trades y si ese corte mejora o no el edge.

## Fase 3 — Entrada vs Salida

**Meta**: confirmar si el bot pierde por mala entrada o mala salida.

### Pasos
- [ ] Medir MFE/MAE en ganadores y perdedores (ya iniciado).
- [ ] Confirmar distribucion por razon de salida (`Hard SL`, `Trailing`, `Time Limit`).
- [ ] Si la mayoria de perdidas tienen MFE bajo, el fallo es entrada.
- [ ] Si muchas perdidas tuvieron MFE alto y luego cerraron mal, el fallo es salida.

### Criterio de exito
- Saber si atacar primero entrada o exit engine.

## Fase 4 — Calibrar Score

**Meta**: validar si la confianza sirve o esta rota.

**Estado actual**: rota/invertida en bootstrap. La causa exacta esta documentada en Fase 0: `heuristic_confidence = 48 + hits*8`, donde los hits premian confluencias trend-following que en regimen lateral/bajista llegan tarde.

### Pasos
- [ ] Medir winrate por bucket de `prob_final`: 55-60, 60-65, 65-70, 70-75, 75-80, 80+.
- [ ] Comparar `ml_pure_prob` vs `prob_final` vs `prob_final` post-filtros.
- [ ] Buscar inflacion del score: donde sube, que regla/consenso lo infla.
- [ ] Si score alto no mejora winrate, esta mal calibrado.
- [ ] Medir cada `heuristic_hit` individual (`EMA_ALIGN`, `ADX_OK`, `RSI_OK`, `VOL_OK`, `ATR_OK`) contra WR/avg PnL; quitar o invertir hits con edge negativo.
- [ ] Validar especificamente si `ATR_OK` debe cambiar de baja volatilidad a umbral minimo (`atr_pct >= MIN_ATR_PCT`).

### Criterio de exito
- Confirmar que bucket 80% realmente gana mas que bucket 70%.

## Fase 5 — Edge por Regimen

**Meta**: validar si la estrategia tiene ventaja en ciertos regimenes.

### Pasos
- [ ] Separar trades por `RANGE`, `BULL_TREND`, `BEAR_TREND`.
- [ ] Medir por regimen: winrate, avg pnl, salida dominante, side dominante.
- [ ] Si `RANGE` no tiene edge, degradarlo a observacion o endurecer solo ese regimen.

### Criterio de exito
- Saber si el problema esta concentrado en `RANGE`.

## Fase 6 — Experimentos Controlados

**Meta**: validar reparaciones sin mezclar causas.

### Experimentos
- **Exp A**: baseline actual (sin cambios).
- **Exp B**: relajar solo `SIDE_PARITY`.
- **Exp C**: relajar solo `COHERENCIA`.
- **Exp D**: desactivar bootstrap y usar modelo real, manteniendo filtros.
- **Exp E**: separar `RANGE` y operar mas restrictivo solo ahi.
- **Exp F**: recalibrar `prob_final` con datos nuevos.

### Regla
- Un cambio por experimento.
- Misma ventana temporal y mismo modo `SHADOW`.
- Medir 48-72h por experimento.

## Fase 7 — Ablacion de Filtros (Plan Aprobado)

**Meta**: desnudar el sistema de filtros, observar flujo base y reconstruir capa por capa hasta identificar donde se rompe.

### Experimento 1 — Ablacion de filtros de calidad (EN EJECUCION)

**Estado**: APLICADO en `.env` el 2026-07-09. Listo para correr ventana de observacion.

**Filtros pausados en `.env`**:
- `DIRECTIONAL_COHERENCE_FILTER=false`
- `SIDE_PARITY_FILTER_ENABLED=false`
- `GLOBAL_FEAR_GREED_FILTER_ENABLED=false`
- `GLOBAL_BTC_DOM_FILTER_ENABLED=false`
- `HMM_RANGE_VETO=false`
- `OI_FILTER_ENABLED=false`
- `CVD_FILTER_ENABLED=false`
- `MTF_FILTER_ENABLED=false`
- `EMA_ALIGNMENT_FILTER_ENABLED=false`
- `EMA_SLOPE_FILTER_ENABLED=false`
- `BREAKOUT_WATCH_ENABLED=false`

**Mantener intacto**:
- `PAPER_MODE=true`, `EXECUTION_BACKEND=live`
- `SHADOW_MODE_MIN=55.0`, `REAL_MODE_THRESHOLD=70.0`
- `MAX_ENTRY_SL_PCT=3.5`, `SHOCK_MIN_DIST_PCT=0.18`, `MIN_RISK_REWARD_RATIO=1.5`
- bootstrap 4/5 intacto
- guardas runtime: `HALT`, `INTEGRITY_LOCK`, cooldowns, limits, reconciliacion, watchdog, SL/TP

**Veto embebido convertido en flag**:
- `MARKET_BREADTH_FEAR`: ahora controlado por `MARKET_BREADTH_FEAR_FILTER_ENABLED`.
- En Experimento 3 queda pausado: `MARKET_BREADTH_FEAR_FILTER_ENABLED=false`.

**Ventana**: 24h minimo, ideal 48h.

**Metricas a capturar**:
- señales totales
- trades `SHADOW` ejecutados
- winrate
- avg pnl
- % Hard SL
- MFE/MAE promedio
- trades por regimen
- trades por simbolo
- razones de veto restantes
- cantidad de `BOOTSTRAP NO_FIRE`

### Matriz de decision post-Experimento 1

| Escenario | Diagnostico | Siguiente paso |
| :--- | :--- | :--- |
| A: suben mucho trades, bajan vetos | cuello en filtros | reactivar filtros uno por uno |
| B: pocos trades, mucho `BOOTSTRAP NO_FIRE` | cuello en bootstrap | Experimento 2 (relajar bootstrap SHADOW) |
| C: mas trades, winrate colapsa | señal base mala | revisar indicadores/bootstrap thresholds |
| D: mas trades, calidad parecida o mejor | sobre-filtrado | usar baseline minima como referencia |
| E: persisten vetos "apagados" | capas embebidas | localizar veto residual |
| F: mucho RANGE y mal resultado | edge pobre en RANGE | separar por regimen |

### Experimento 2 — Relajar bootstrap para SHADOW (EN EJECUCION)

**Estado**: APLICADO el 2026-07-09. Listo para correr ventana de observacion.

**Trigger**: Experimento 1 confirmo Escenario B (bootstrap como cuello principal: 13/23 señales en `BOOTSTRAP NO_FIRE`).

**Cambios aplicados**:
- `core/config/manager.py`: añadido `BOOTSTRAP_SHADOW_MIN_HITS = _env_int("BOOTSTRAP_SHADOW_MIN_HITS", 4)` (default preserva comportamiento original).
- `core/signals/filters.py:235`: `bootstrap_ready_shadow` ahora usa `Config.BOOTSTRAP_SHADOW_MIN_HITS` en vez de hardcoded `4`.
- `.env`: `BOOTSTRAP_SHADOW_MIN_HITS=3` para el experimento.
- `bootstrap_ready_real` sigue en `5` (REAL no se toca).

**Mantener intacto**:
- `bootstrap_ready_real` = `5/5` reglas (no se relaja REAL).
- Todos los filtros de calidad siguen pausados (Experimento 1).
- `BEAR_REVERSAL_VETO` sigue pausado (`MARKOV_PREVETO_BEARISH_REVERSAL_MIN=100.0`).
- Seguridad runtime intacta.

**Ventana**: 24h minimo, ideal 48h.

**Metricas a capturar**: mismas que Experimento 1.

**Interpretacion**:
- Si suben mucho los trades SHADOW: el cuello era bootstrap. Usar esta baseline como referencia.
- Si siguen pocos trades: hay mas capas embebidas bloqueando. Escenario E.
- Si suben mucho pero winrate colapsa: la heuristica base no tiene edge. Escenario C.

### Checklist de lectura post-experimento

Tras la ventana de observacion, ejecutar:

1. Consultar `signal_alerts` en `tools/sniper_brain.db`:
   - total de señales en la ventana
   - `%` por `execution_mode`
   - `%` por `status`
   - ranking de `filter_reason` residuales
   - ranking de `audit_verdict` residuales
   - ranking de `BOOTSTRAP NO_FIRE` vs `BOOTSTRAP SHADOW`

2. Consultar `trades` en `tools/sniper_brain.db`:
   - trades nuevos en la ventana
   - winrate
   - avg pnl
   - `%` Hard SL
   - MFE/MAE promedio
   - distribucion por regimen
   - distribucion por simbolo
   - distribucion por razon de salida

3. Comparar contra Snapshot Fase 0 (ver seccion mas abajo).

4. Clasificar el resultado en Escenario A-F y decidir siguiente paso.

## Orden Recomendado

1. Fase 1 — salir de bootstrap.
2. Fase 2 — rankear filtros.
3. Fase 3 — confirmar entrada vs salida.
4. Fase 4 — validar score.
5. Fase 5 — medir por regimen.
6. Fase 6 — diseñar A/B tests.

## Experimento 3 — Reactivar coherencia direccional (EN EJECUCION)

**Estado**: APLICADO el 2026-07-09.

**Cambio aplicado**:
- `.env`: `DIRECTIONAL_COHERENCE_FILTER=true`.

**Mantener igual**:
- `BOOTSTRAP_SHADOW_MIN_HITS=3`.
- `MARKOV_PREVETO_BEARISH_REVERSAL_MIN=100.0`.
- `MARKET_BREADTH_FEAR_FILTER_ENABLED=false`.
- `SIDE_PARITY_FILTER_ENABLED=false`.
- Filtros macro, HMM range, OI/CVD/MTF, EMA alignment/slope y breakout watch siguen pausados.

**Objetivo**:
- Medir si bloquear operaciones contra `current_sentiment` reduce basura sin secar el flujo.
- Con sentiment alcista, se espera que bloquee la mayoria de `SELL` y deje pasar `BUY`.

**Criterio**:
- Mantener si reduce Hard SL / mejora avg pnl o MFE sin colapsar volumen.
- Apagar si recorta volumen sin mejorar calidad.

## Criterio Global De Exito

- Salir de bootstrap.
- Reducir vetos inutiles.
- Mejorar winrate SHADOW.
- Bajar porcentaje de trades que terminan en `Hard SL`.
- Lograr que buckets altos de confianza ganen mas que los bajos.
- No pasar a `REAL` hasta cumplir todo lo anterior.

## Snapshot Fase 0

### Config efectiva cargada (2026-07-09)
```
PAPER_MODE=True
EXECUTION_BACKEND=live
REAL_MODE_THRESHOLD=70.0
SHADOW_MODE_MIN=55.0
SHADOW_MODE_MAX=69.9
MAX_ENTRY_SL_PCT=3.5
SHOCK_MIN_DIST_PCT=0.18
MIN_RISK_REWARD_RATIO=1.5
RISK_REWARD_HIGH_VOL_MIN_RATIO=1.7
RISK_REWARD_FILTER_ENABLED=True
OI_FILTER_ENABLED=True
DIRECTIONAL_COHERENCE_FILTER=True
SIDE_PARITY_FILTER_ENABLED=True
EMA_ALIGNMENT_FILTER_ENABLED=True
EMA_SLOPE_FILTER_ENABLED=True
HMM_RANGE_VETO=True
HMM_RANGE_PENALTY=0.8
ADX_TREND_THRESHOLD=17
SIDE_PARITY_MIN_ADX=22.0
SIDE_PARITY_MIN_VOL_REL=0.8
SIDE_PARITY_MIN_AGENT_SUPPORT=2
GLOBAL_FEAR_GREED_FILTER_ENABLED=True
GLOBAL_FEAR_VETO_THRESHOLD=20
REQUIRE_GHOST_MODEL_FOR_TRADING=False
SIGNAL_AGENT_OVERRIDE_ENABLED=True
SIGNAL_AGENT_OVERRIDE_THRESHOLD=15.0
MAX_OPEN_TRADES=10
MAX_SHADOW_TRADES=20
RISK_PER_TRADE_PERCENT=0.5
MAX_RISK_USD=5.0
```

### Estado de modelos
- No existe `agent_models.pkl`.
- No existe `ghost_brain.pkl`.
- No existe `ghost_brain_pro.pkl`.
- No existe `ghost_brain_advanced.pkl`.
- No existe `lstm_model.h5` + `scaler.pkl`.
- `core/bot_models_startup.py` cae a `bootstrap_heuristic_mode=True` cuando no hay modelo.

### Estado de DB (`tools/sniper_brain.db`)
- 49 trades SHADOW, 13 wins, 36 losses.
- Winrate 26.5%, avg pnl -2.06%.
- 4161 signal_alerts: 4062 DISCARDED (BOOTSTRAP_NONE), 99 PENDING (88 SHADOW, 11 PAPER).
- Hard SL: 25 trades, 0% winrate, -5.56% avg.
- Trailing BE: 10 trades, 100% winrate, +4.93% avg.
- Time Limit 60m: 6 trades, 0% winrate, -3.35% avg.
- 48/49 trades en RANGE.

### Vetos dominantes (`signal_alerts` reciente)
| Filtro | Vetos |
| :--- | :--- |
| COHERENCIA (BUY en bajista + SELL en alcista) | 692 |
| MARKET_BREADTH_FEAR | 318 |
| Filter Pass (v118-PRO) | 251 |
| RANGE_BREAKOUT_ANTICIPATION | 216 |
| SIDE_PARITY (variantes) | ~430 |
| HMM_RANGE_PENALTY | 125 |

### Confianza
- avg win conf 74.38 vs avg loss conf 74.41 (no separa).
- bucket 80%: winrate 9.1% (inflado/inverso).

### Entrada vs salida
- 34/36 perdidas (94.4%) con MFE <= 0.5%.
- 24/36 perdidas (66%) con MFE <= 0.2%.
- Indicacion fuerte: la mayoria de los trades perdedores casi nunca fueron buenos desde el inicio. Fallo de entrada, no de salida.

---

## Fase Torniquete — Plan de Estabilización (APPIED 2026-07-10)

**Trigger**: Analisis de 183 trades SHADOW (Jul 9-10) revelo:
- WR 26.5%, avg PnL -1.76%, 93% de perdidas con MAE<=0.5% en precio.
- Causa raiz confirmada: baja volatilidad + correlacion de clusters (13 BUY simultaneos).
- Hallazgo adicional: confianza invertida (33.3% WR en bucket 65-68 vs 15.0% en 80-90).

**Expert externo (cuant)** corrigio mi diagnostico:
- MAE tracking NO esta roto. 0.3% MAE en precio = -5.1% PnL con 10x leverage + fees.
- Confianza invertida: la heuristica bootstrap recompensa atributos no predictivos.
- ATR pct > 0.7% es el separador real: 25.9% WR vs 8.3% debajo de 0.7%.

### Cambios aplicados

| Cambio | Archivos | Valor | Efecto esperado |
| :--- | :--- | :--- | :--- |
| MIN_ATR_PCT filter | manager.py, filters.py, .env | 0.006 (0.6%) | Elimina ~11% de trades de baja volatilidad (WR 8.3%). Mejora WR general ~2-3%. |
| Cap direccional SHADOW | trade_entry.py, .env | MAX_SHADOW_DIRECTIONAL_TRADES=3 | Evita cluster de >3 trades en misma direccion. Previene drawdowns correlacionados. |
| HARD_SL ajustado | manager.py, .env | -3.5% (antes -5.0%) | Reduce max loss por trade de -5.1% a -3.5%. |
| MAX_SHADOW_DIRECTIONAL_TRADES flag | manager.py, .env | 3 | Afirmativo. |

### Hallazgos documentados

**Confianza invertida** (bucket vs WR):
- [65-68]: 9 trades, 33.3% WR, +0.52% avg ← MEJOR
- [68-70]: 6 trades, 0.0% WR, -4.74% avg ← PEOR (n pequeno)
- [70-72]: 34 trades, 26.5% WR, -1.39% avg
- [72-75]: 54 trades, 24.1% WR, -2.02% avg
- [75-80]: 23 trades, 21.7% WR, -1.88% avg
- [80-90]: 40 trades, 15.0% WR, -2.42% avg ← PEOR en volumen

Conclusion: la heuristica bootstrap es contraproducente. MIN_ATR_PCT mitiga al filtrar el ruido que infla heuristic_hits.

**Desalineacion ATR SL vs HARD_SL** (no resuelto):
- ATR SL = entry - ATR x 2.0 = 1-2% price distance = 10-20% PnL con 10x leverage.
- HARD_SL = -3.5% = ~0.35% price move.
- El HARD_SL siempre dispara primero. El ATR SL es irrelevante para shadow.
- Solucion futura: reducir STOP_LOSS_ATR_MODIFIER a ~0.6 o reducir leverage.

### Plan de medicion post-aplicacion

1. Encender bot con config actualizada.
2. Medir 50-100 trades SHADOW cerrados.
3. Comparar contra baseline (Fase 0):
   - WR: objetivo >30% (baseline 26.5%).
   - Avg PnL: objetivo >-1.0% (baseline -1.76%).
   - % HARD_SL: objetivo <60% (baseline 51%).
   - % DYNAMIC_SL: objetivo >20% (actual ~15%).
4. Si WR<30%, evaluar reducir STOP_LOSS_ATR_MODIFIER.
5. Si WR>30%, mantener como baseline y reintroducir filtros uno por vez.

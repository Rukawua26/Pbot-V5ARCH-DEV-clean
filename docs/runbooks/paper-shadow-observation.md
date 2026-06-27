# PAPER/SHADOW Observation Runbook

Guia para validar mejoras pendientes sin tocar trading real.

## Alcance

- `FVG_TRACKER_ENABLED`: medir gaps como modulo satelite read-only.
- `GLOBAL_MARKET_PROVIDER_ENABLED`: enriquecer contexto con datos macro read-only.
- `GLOBAL_FEAR_GREED_FILTER_ENABLED` y `GLOBAL_BTC_DOM_FILTER_ENABLED`: validar thresholds macro.
- `SIGNAL_AGENT_OVERRIDE_ENABLED`: observar direccion por consenso de agentes.
- Dashboard API: requiere `SNIPER_API_KEY` de al menos 16 caracteres.

## Reglas

- Ejecutar solo con `PAPER_MODE=true` o SHADOW sin `ALLOW_REAL_TRADING=true`.
- No integrar FVG, macro o consenso al Risk Engine ni sizing sin evidencia estadistica.
- No ajustar thresholds por intuicion; comparar contra baseline previo.
- Si aparece estado ambiguo en `REAL`, priorizar `HALT` y reconciliacion, no observacion.

## Preflight

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/pending_improvements_readiness.py
```

El script no llama a Binance, CoinGecko ni arranca el bot. Solo lee `.env` y variables de entorno.

## Configuracion PAPER sugerida

```env
PAPER_MODE=true
ALLOW_REAL_TRADING=false
FVG_TRACKER_ENABLED=true
GLOBAL_MARKET_PROVIDER_ENABLED=true
GLOBAL_FEAR_GREED_FILTER_ENABLED=true
GLOBAL_BTC_DOM_FILTER_ENABLED=true
SIGNAL_AGENT_OVERRIDE_ENABLED=true
```

## Evidencia minima

- FVG: alertas totales, falsos positivos, fills posteriores, timing util.
- Global Market: frecuencia de huecos de datos, cache hits, errores de rate limit.
- Macro filters: cantidad de vetos/boosts y resultado de trades evitados o potenciados.
- Consenso/trailing: al menos 10 trades SHADOW cerrados post-cambio.

## Criterios de avance

- FVG solo puede pasar de observacional a candidato si aporta informacion incremental medible.
- Global Market queda aceptado si no introduce huecos frecuentes ni latencia operativa relevante.
- Macro filters requieren evidencia de mejora sin degradar drawdown.
- Consenso/trailing requiere winrate SHADOW >45% y mejor relacion avg win/avg loss contra baseline.

# Tech Stack

## Tecnologias Y Versiones

- Runtime: Python 3.12.
- Exchange: ccxt v4.5 con Binance Futures.
- ML/AI: scikit-learn, xgboost, lightgbm, hmmlearn, imbalanced-learn.
- Technical analysis: ta, pandas-ta.
- Data: pandas, pyarrow.
- Dashboard/API: FastAPI, uvicorn.
- Config: python-dotenv, config multi-capa en `core/config/`.
- Testing/quality: unittest, coverage gate 75%, ruff, mypy, pip-audit.
- Infra: Docker, docker-compose.

## Estructura Del Proyecto

- `main.py`: entrypoint minimo; carga `.env` y llama `core.bot_app.run_entrypoint`.
- `core/`: runtime principal del bot.
- `core/config/`: configuracion real; `config.py` raiz es proxy legacy.
- `core/analytics/`: modulos satelite/observacionales como FVG Tracker.
- `core/providers/`: proveedores externos como global market provider.
- `core/signals/`: filtros, contexto y analisis de senales.
- `core/strategy/`: orquestacion y utilidades de estrategia.
- `tools/`: scripts operativos, validacion, notifier, chaos/recovery drills.
- `tests/`: suite unittest y regresiones.
- `docs/engineering/memoria-tecnica.md`: memoria tecnica e invariantes.
- `docs/roadmap/mejoras-pendientes.md`: mejoras pendientes y condiciones de activacion.

## Arquitectura Y Flujo De Datos

- `core/bot_app.py` construye `Bot` y cablea runtime, loops, dashboard, Telegram, wallet sync y servicios.
- `core/bot_facade.py` reexporta `Bot` como contrato publico.
- `core/bot_connection.py` separa `PAPER`, `SHADOW` y `REAL`.
- `core/execution_adapters.py` contiene la frontera `shadow_live`; no mezclar simulacion con ejecucion real fuera de ahi.
- `core/risk_engine.py` calcula riesgo y sizing.
- `core/trade_entry.py` controla entrada; `similarity_boost` debe afectar el sizing.
- `tools/dashboard_api_server.py` expone FastAPI en `127.0.0.1:8000` para el dashboard web.
- `dashboard/static/index.html` es el frontend SPA operativo con Tailwind CDN y Chart.js.
- El dashboard es un servicio separado del bot; se inicia independientemente con `dashboard/run.sh`.
- El exchange manda sobre la DB para exposicion real, ordenes y posiciones.
- En `REAL`, estado live ambiguo debe preferir `HALT` y reconciliacion.
- Modulos satelite deben ser read-only por defecto y no influir en risk/sizing/ejecucion sin evidencia.

## Convenciones

- Usar la venv local: `./.venv/bin/python`.
- Cambios pequenos y enfocados; no mezclar refactors amplios con fixes funcionales.
- Tests con `SNIPER_DISABLE_FILE_TELEMETRY=1` cuando aplique.
- Runtime critico requiere lectura proporcional de `docs/engineering/memoria-tecnica.md`.
- Si se toca `main.py`, `Bot`, `BotFacade` o imports modulares, correr regression/smoke contracts.
- Si se corrige una regresion o regla preventiva, registrar en memoria tecnica cuando aplique.
- Mantener coverage gate en 75% salvo spec explicita.

## Comandos

```bash
./.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.lock
./.venv/bin/python main.py
dashboard/run.sh
./.venv/bin/python -m pip check
./.venv/bin/python -m compileall -q main.py core
./.venv/bin/ruff check core/ tests/
./.venv/bin/ruff format --check core/ tests/
MYPYPATH=. ./.venv/bin/mypy --explicit-package-bases core/config/ core/types.py core/bot_facade.py core/execution_adapters.py
PYTHON_BIN=./.venv/bin/python bash scripts/smoke_modular_imports.sh
./.venv/bin/python tools/check_no_silent_pass.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/chaos_matrix.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/recovery_drill.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m coverage run -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m coverage report --fail-under=75
docker build -t sniper-ai .
docker compose up -d
docker compose logs -f sniper-ai
docker compose down
```

## Nivel SDD

- Nivel: Spec-Anchored.
- La spec se mantiene viva y actualizada.
- Cada cambio importante se registra en spec o plan antes que en codigo.
- Specs en: `spec/constitution/` y `spec/features/`.

## Estilo Visual

- Dashboard web: tema oscuro por defecto, enfoque operacional, denso y orientado a trading/riesgo.
- Paleta semantica: blue=informacion/radar, green=ganancia/ok, red=riesgo/perdida/HALT, amber=advertencia/SHADOW.
- Tipografia: `JetBrains Mono` para datos/metricas y `Inter` para interfaz.
- Charts con Chart.js; tablas y estados deben priorizar trazabilidad, filtros visibles y accion rapida.
- Mensajes operativos deben diferenciar claramente `PAPER`, `SHADOW` y `REAL`.

## Prohibiciones

- No dejar posiciones reales sin `HARD SL`.
- No degradar `REAL` a endpoints publicos ante fallos de auth/permisos.
- No agregar retries no idempotentes que puedan duplicar exposicion.
- No mezclar `PAPER`, `SHADOW` y `REAL`.
- No introducir `pass` silenciosos en `core/`.
- No exponer el dashboard sin `SNIPER_API_KEY` configurada.
- No mezclar el dashboard con el runtime del bot en el mismo proceso salvo arquitectura existente/documentada.
- No ampliar `core/bot_facade.py` para notificaciones satelite sin decision arquitectonica explicita.
- No integrar FVG/gaps en risk, sizing, entradas o salidas sin evidencia estadistica.
- No bajar `MAX_ENTRY_SL_PCT` bajo `3.0` sin validar ATR promedio.
- No subir `SHOCK_MIN_DIST_PCT` sobre `0.2` sin medir falsos positivos.
- No activar `REQUIRE_GHOST_MODEL_FOR_TRADING=True` sin modelo y logging visible.
- No subir `MIN_NOTIONAL_VALUE` sin validar balance por leverage.
- No commitear `.env`, bases `.db`, logs, reportes locales ni artefactos con datos privados.

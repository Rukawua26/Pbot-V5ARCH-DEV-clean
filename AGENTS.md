# AGENTS

## Knowledge Base
- Nota de proyecto en `~/docs/agent-context/summaries/pbot.md` (con YAML frontmatter y wikilinks).
- MOC central: `~/docs/_MOC.md`.
- Para trabajo Python, ejecuta `python3 "/home/miguel/Aguia refactor aprendizaje/ask.py" "<palabras clave breves>" --language python`; sigue el estado/acción de evidencia, nunca precargues/indexes la guía y prioriza siempre reglas, contratos, invariantes y tests del repositorio.

## Fuentes De Verdad

- Bot de trading Binance Futures con modos `PAPER`, `SHADOW` y `REAL`; cambios en ejecución, órdenes, posiciones, wallet sync, reconciliación, watchdog, recovery, `HALT` o stop loss son runtime crítico.
- Antes de editar código, lee `docs/engineering/memoria-tecnica.md` con profundidad proporcional al riesgo; para runtime crítico revisa también `.opencode/context/known-bugs.md`.
- Si implementas o evalúas una mejora pendiente, lee primero `docs/roadmap/mejoras-pendientes.md`.
- CI real vive en `.github/workflows/ci.yml`; si contradice README/docs, prioriza CI y scripts ejecutables.

## Arquitectura Sensible

- `main.py` es deliberadamente mínimo: carga `.env` y llama `run_entrypoint` desde `core.bot_app`; no metas bootstrap pesado ahí.
- `core/bot_app.py` construye `Bot` y cablea runtime, loops, dashboard, Telegram, wallet sync y servicios.
- `config.py` es proxy legacy; la configuración real está en `core/config/manager.py` y `core/config/operational.py`; `.env` se carga en `main.py` y como fallback en `operational.py`.
- `core/bot_facade.py` reexporta `Bot` como contrato público; cambios en `Bot`, `BotFacade` o `main.py` requieren `tools/regression_contracts.py`.
- `core/bot_connection.py` separa modos: `PAPER` puede continuar con endpoints públicos; `REAL` requiere `ALLOW_REAL_TRADING=true`, keys válidas y permisos Futures, y debe abortar ante auth/permisos inválidos.
- `core/execution_adapters.py` contiene `shadow_live`; no mezcles simulación shadow con ejecución real fuera de esa frontera.
- `tools/learning.py` es el `Brain` usado en runtime; `learning.py` raíz no es la fuente runtime.

## Invariantes Operativos

- El exchange manda sobre DB para exposición real y estado de órdenes/posiciones.
- Nunca dejes una posición real sin `HARD SL`; ante estado live ambiguo, prioriza `HALT` y reconciliación.
- No agregues retries no idempotentes que puedan duplicar exposición.
- Mantén separación estricta entre `PAPER`, `SHADOW` y `REAL`.
- No introduzcas `pass` silenciosos en `core/`; `tools/check_no_silent_pass.py` y pre-commit lo bloquean.
- En `core/trade_entry.py`, no muevas similarity search después del sizing: `similarity_boost` debe afectar el tamaño.
- Conserva reglas conocidas: no bajar `MAX_ENTRY_SL_PCT` bajo `3.0`, no subir `SHOCK_MIN_DIST_PCT` sobre `0.2`, no activar `REQUIRE_GHOST_MODEL_FOR_TRADING=True` sin modelo y logging visible, y no subir `MIN_NOTIONAL_VALUE` sin validar balance por leverage.

## Locks

- Si un flujo necesita varios locks, usa este orden ascendente: `bot.lock`, `execution._exchange_call_lock`, `execution._account_lock`, `shadow._lock`, `bot.db_lock`, `bot.price_lock`.
- No adquieras un lock anterior mientras mantienes uno posterior; `bot_wallet_sync.py` puede ejecutarse desde guardian/runtime loops y debe respetar ese orden.

## Skills OpenCode

- No cargues skills por defecto; sigue `.opencode/context/skill-policy.md`.
- Usa las skills curadas de `.opencode/skills`: `runtime-ops-and-trading-safety`, `security-and-hardening`, `repo-validation`, `python-testing`, `opencode-customization` según el disparador.
- No uses el directorio raíz `skills/` como fuente de skills OpenCode; contiene material más amplio y aumenta contexto.

## Comandos

- Usa la venv local cuando exista: `./.venv/bin/python`.
- Instalar como CI: `./.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.lock`.
- Test puntual: `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest tests/test_bot_security_runtime.py`.
- Suite unitaria: `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"`.
- Si cambias bootstrap/imports modulares: `PYTHON_BIN=./.venv/bin/python bash scripts/smoke_modular_imports.sh`.
- Si cambias contratos de `main.py`, `Bot` o `BotFacade`: `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py`.
- Instalar auditoria manual: `./.venv/bin/python -m pip install -r requirements-audit.lock`.
- Auditoria manual de mutacion del Risk Engine: `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/mutmut run`; revisar con `./.venv/bin/mutmut results`. No usar como gate de CI.

## Validación CI

```bash
./.venv/bin/python -m pip check
./.venv/bin/python -m pip_audit --strict
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
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest tests/test_temporal_invariance.py
docker build -t sniper-ai .
```

## Límites

- Haz cambios pequeños; no mezcles refactors amplios con fixes funcionales.
- No borres código legacy sin verificar compatibilidad o recovery.
- No commitees `.env`, bases `.db`, logs, reportes locales ni artefactos generados con datos privados.

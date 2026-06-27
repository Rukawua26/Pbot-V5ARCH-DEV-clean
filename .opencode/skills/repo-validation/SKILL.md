---
name: repo-validation
description: Use when validating changes, closing a task, choosing test commands, checking CI parity, or when the user asks to run tests, smoke tests, compile checks, or regression contracts.
---

# Repo Validation

Prefer the local venv: `./.venv/bin/python`.

Base sequence aligned with CI:

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

Use narrower tests when appropriate, but report what was skipped and why.

Always run `scripts/smoke_modular_imports.sh` after bootstrap/import changes.

Always run `tools/regression_contracts.py` after changes to `main.py`, `Bot`, or `BotFacade` contracts.

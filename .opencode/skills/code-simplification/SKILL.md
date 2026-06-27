---
name: code-simplification
description: Use when removing dead code, cleaning unused imports, deleting orphaned files, simplifying complex logic, or reducing technical debt without changing behavior. Safe for Python.
---

# Code Simplification

Simplify without changing behavior. Tests must pass after every change.

## Dead Code Removal

Check before removing anything (`Chesterton's Fence`):

- Is it imported/used anywhere? (grep `core/` and `tools/`)
- Is it referenced dynamically? (configs, scripts, `getattr`, method resolution)
- When was it last modified? (git blame)
- Are there tests covering it?

Priority: imports muertos > archivos huérfanos > funciones sin caller > bloques comentados.

## Safety Rules

- Never touch runtime-critical files without running `runtime-ops-and-trading-safety` first
- Never change behavior — only remove what is confirmed unused
- One simplification per commit; run tests after each
- Do not remove error handling or weaken validation
- Do not refactor code outside the scope of the task

## Verification

```bash
./.venv/bin/python -m compileall -q main.py core/
./.venv/bin/python tools/check_no_silent_pass.py
PYTHON_BIN=./.venv/bin/python bash scripts/smoke_modular_imports.sh
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

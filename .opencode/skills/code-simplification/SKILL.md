---
name: code-simplification
description: Use ONLY for behavior-preserving cleanup: removing confirmed dead code, unused imports, orphaned files, duplicate constants, or simplifying existing logic without feature/runtime behavior changes.
---

# Code Simplification

Simplify without changing behavior. The goal is less code, fewer duplicated paths,
and clearer ownership without weakening runtime safety.

Use this skill for maintainability work only. If the change touches execution,
orders, positions, wallet sync, reconciliation, watchdog, recovery, HALT, stop
loss, `core/bot_guardian.py`, `core/trade_entry.py`, `core/trade_exit.py`,
`core/execution_service.py`, or `core/bot_wallet_sync.py`, also load
`runtime-ops-and-trading-safety` and keep the diff especially small.

## Audit Workflow

Start read-only and produce a ranked map before editing:

1. Run repository checks that reveal already-enforced cleanup issues:
   - `./.venv/bin/ruff check core/ tests/`
   - `./.venv/bin/python tools/check_no_silent_pass.py`
2. Search for obvious debt:
   - `TODO|FIXME|HACK|XXX`
   - `^\s*pass\b`
   - broad legacy imports like `from config import Config`
3. Measure complexity with AST or repo tooling, not guesses:
   - functions over 150 lines
   - `try` blocks over 50 lines
   - modules over 500 lines
4. For every removal candidate, prove it is unused with grep plus caller checks.
5. Prefer a written cleanup queue when a file is runtime-critical. Do not refactor
   critical flows in the same change as functional fixes.

Keep long scripts out of the skill body. Use ad-hoc AST one-liners only when
needed, or move stable analysis helpers to `tools/` or `.opencode/context/`.

## Dead Code Removal

Check before removing anything (`Chesterton's Fence`):

- Is it imported/used anywhere? (grep `core/` and `tools/`)
- Is it referenced dynamically? (configs, scripts, `getattr`, method resolution)
- When was it last modified? (git blame)
- Are there tests covering it?

Priority:

- Unused imports already detected by ruff.
- Confirmed orphan files or functions.
- Duplicate constants/config definitions.
- Commented-out code blocks.
- Large-function extraction only when behavior can be pinned by focused tests.

## Safety Rules

- Never touch runtime-critical files without running `runtime-ops-and-trading-safety` first
- Never change behavior — only remove what is confirmed unused
- One simplification per commit; run tests after each
- Do not remove error handling or weaken validation
- Do not refactor code outside the scope of the task
- Do not normalize legacy imports repo-wide unless contracts and bootstrap tests are run
- Do not delete legacy compatibility modules (`config.py`, `core/bot_facade.py`) unless an explicit migration plan exists
- Do not split huge runtime functions opportunistically; first add/verify tests around the exact behavior to preserve

## Report Format

When auditing, return findings like this:

- `file.py:line` — severity — issue.
- Evidence: caller/import/search result or measured size.
- Safe next step: smallest behavior-preserving cleanup.
- Validation: focused tests or CI commands required.

Severity guidance:

- High: cleanup risk blocks maintainability in runtime-critical files or duplicates config with operational impact.
- Medium: large functions, duplicated helpers, unclear ownership, missing tests before simplification.
- Low: style-only simplification, comments, naming, non-runtime tooling cleanup.

## Repo-Specific Watchlist

- `core/trade_entry.py::execute_order` is intentionally risky to refactor; split only with tests around PAPER/SHADOW/REAL, HARD SL failure, sizing, and similarity boost.
- `core/bot_guardian.py::run_guardian_loop` is runtime-critical; isolate handlers one at a time and keep HALT behavior unchanged.
- `core/trade_exit.py::close_trade` and `core/reconciliation.py::reconcile_bootstrap_state` must preserve exchange-as-source-of-truth behavior.
- `config.py` is a legacy public proxy. Removing or bypassing it broadly requires `tools/regression_contracts.py` and `scripts/smoke_modular_imports.sh`.
- `tools/learning.py` is the runtime `Brain`; do not confuse it with docs that mention a root `learning.py`.
- `.opencode/context/known-bugs.md` should be updated when simplification prevents a known regression.

## Verification

```bash
./.venv/bin/python -m compileall -q main.py core/
./.venv/bin/ruff check core/ tests/
./.venv/bin/ruff format --check core/ tests/
./.venv/bin/python tools/check_no_silent_pass.py
PYTHON_BIN=./.venv/bin/python bash scripts/smoke_modular_imports.sh
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

For runtime-critical simplification, also run focused tests for the touched area
and `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py`
when public contracts or bootstrap imports are touched.

When simplifying files under `tools/`, run ruff only on the touched tool files.
The whole `tools/` tree currently contains legacy style debt and is not a safe
default gate for unrelated core changes.

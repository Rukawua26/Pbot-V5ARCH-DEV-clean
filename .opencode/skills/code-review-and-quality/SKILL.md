---
name: code-review-and-quality
description: Use before merging any change. Reviews correctness, readability, architecture, security, and performance.
---

# Code Review and Quality

Multi-axis review — approve when the change improves overall code health.

## Axes

| Axis | What to check |
|------|---------------|
| Correctness | Edge cases, error handling, idempotency, no behavior change |
| Readability | Naming, structure, unnecessary complexity |
| Architecture | Follows patterns in AGENTS.md, stays within module boundaries |
| Security | No secrets leaked, no broad permissions, no untrusted input |
| Performance | No N+1 queries, no blocking calls in async paths, no unecessary API calls |

## Project-Specific Gates

- Runtime-critical changes must load `runtime-ops-and-trading-safety`
- Silent `pass` in `core/` is blocked by CI
- Always run `regression_contracts.py` when `main.py`, `Bot`, or `BotFacade` changes
- Run `smoke_modular_imports.sh` when bootstrap/imports change
- No mixing refactors with functional fixes in the same commit

## When to Block

- Change breaks tests without clear justification
- Change weakens error handling or removes HARD SL coverage
- Change introduces non-idempotent retries that can duplicate exposure
- Change mixes refactor with feature work

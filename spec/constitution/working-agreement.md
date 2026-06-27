# Working Agreement

- Read `docs/engineering/memoria-tecnica.md` before code changes with depth proportional to risk.
- Read `docs/roadmap/mejoras-pendientes.md` before implementing or evaluating pending improvements.
- Treat execution, orders, positions, wallet sync, reconciliation, watchdog, recovery, `HALT`, and stop loss as runtime critical.
- Keep strict separation between `PAPER`, `SHADOW`, and `REAL`.
- In `REAL`, ambiguous live state must prefer `HALT` and reconciliation.
- Never leave a real position without `HARD SL`.
- Do not add non-idempotent retries that can duplicate exposure.
- Do not introduce silent `pass` in `core/`.
- Implement speculative or experimental improvements disabled by default and validate first in `PAPER` or `SHADOW`.
- Record new preventive rules or critical behavior changes in `docs/engineering/memoria-tecnica.md`.

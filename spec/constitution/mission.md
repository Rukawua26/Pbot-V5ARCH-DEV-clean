# Mission

Pbot is a Binance Futures trading bot with `PAPER`, `SHADOW`, and `REAL` modes, ML-based signals, technical analysis, and strict runtime safety controls.

## Goals

- Preserve capital and operational safety above feature velocity.
- Keep `PAPER`, `SHADOW`, and `REAL` behavior strictly separated.
- Ensure every `REAL` position has a hard stop loss.
- Prefer `HALT` and reconciliation over continuing with ambiguous live state.
- Validate strategy improvements statistically before connecting them to risk, sizing, entry, or exit logic.

## Non-Goals

- Do not bypass runtime safety gates for convenience.
- Do not introduce execution logic outside the existing execution boundaries.
- Do not treat experimental signals as production signals without evidence.

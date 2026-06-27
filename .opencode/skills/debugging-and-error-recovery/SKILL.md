---
name: debugging-and-error-recovery
description: Use when tests fail, builds break, runtime errors occur, or behavior doesn't match expectations in the trading bot.
---

# Debugging and Error Recovery

Systematic triage — stop adding features, preserve evidence, find root cause.

## Triage Checklist

1. Reproduce: confirm the error is consistent (not flaky)
2. Isolate: narrow to the smallest failing input or step
3. Capture: logs, stack trace, state at failure time
4. Git bisect: identify the commit that introduced the regression
5. Fix: apply the minimal correction
6. Verify: the failing case passes + suite still passes

## Trading Bot Specifics

- For execution errors: check `execution_adapters.py`, order state, Binance response
- For reconciliation errors: check `bot_wallet_sync.py` and position mismatch
- For HALT/recovery: follow watchdog and recovery flow
- For signal/sizing errors: check filters, risk engine, and context snapshot

## After Fixing

```bash
./.venv/bin/python tools/check_no_silent_pass.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

If the fix is runtime-critical, also load `runtime-ops-and-trading-safety`.

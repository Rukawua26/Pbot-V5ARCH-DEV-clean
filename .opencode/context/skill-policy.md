# Skill Policy

Do not load skills by default. Load a skill only when its trigger matches the current task.

Use `.opencode/skills` as the curated OpenCode skill set for this repository. Do not register the top-level `skills/` directory as an OpenCode skill path because it contains many broader skills and can increase token use.

Skill triggers:

- `runtime-ops-and-trading-safety`: execution, Binance Futures, orders, positions, wallet sync, reconciliation, watchdog, recovery, HALT, stop loss, `core/execution_adapters.py`, `core/bot_connection.py`, `core/bot_app.py`, or `core/bot_facade.py`.
- `security-and-hardening`: secrets, `.env`, API keys, permissions, shell scripts, dependencies, network, MCP, external data, or authentication.
- `repo-validation`: before closing a task, when validation is requested, or when choosing test commands.
- `python-testing`: when creating, changing, or reviewing tests.
- `opencode-customization`: `.opencode/`, `opencode.json`, agents, skills, plugins, MCP, or permission rules.
- `code-simplification`: removing dead code, unused imports, orphaned files, simplifying logic without behavior change.
- `debugging-and-error-recovery`: test failures, build breaks, runtime errors, unexpected behavior in the trading bot.
- `code-review-and-quality`: before merging changes, multi-axis review (correctness, readability, architecture, security, performance).

Keep skill files short. Move stable reference material to `.opencode/context/` instead of expanding skill bodies.

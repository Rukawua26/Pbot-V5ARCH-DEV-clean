# Intelligence Layer

La capa `tools/intelligence/` es consultiva y de solo lectura respecto al runtime del bot.

Fuentes de datos:

- `logs/execution_events.jsonl`
- `/dev/shm/sniper_state.json`
- DB `tools/sniper_brain.db` o `SNIPER_DB_PATH`

Artifacts generados:

- `reports/intelligence/daily_report.json`
- `reports/intelligence/weekly_report.json`
- `reports/intelligence/postmortem_trade_<id>.json`
- advisories persistidos en `advisory_snapshots`

Reglas operativas:

- no bloquea `trade_entry`
- no participa en `REAL` execution
- si falla, el bot sigue igual
- los datos `SHADOW` se usan como señal consultiva y de calibración, no como fuente final de verdad del exchange

Comandos útiles:

```bash
./.venv/bin/python -m tools.intelligence.report_daily
./.venv/bin/python -m tools.intelligence.report_weekly
./.venv/bin/python -m tools.intelligence.postmortem 123
```

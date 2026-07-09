# Runbook Operativo - Sniper AI v118

> Guía de respuesta anteincidentes en PAPER y SHADOW.
> Aplica solo a modos `PAPER` y `SHADOW`. No operar en `REAL` sin validar este runbook primero.

---

## 1. Eventos del Dashboard

### Pestaña Votos / Consenso

| Status | Significado | Accion |
| :--- | :--- | :--- |
| `BLOCKED_NEUTRAL` | Todos los agentes devolvieron 50.0; no hay modelo o datos insuficientes. | Revisar si `ghost_model` está cargado. Si es `bootstrap_heuristic_mode`, es esperado. No requiere accion. |
| `BLOCKED_RISK` | La compuerta de riesgo bloqueo la entrada (`WS_RECONCILIATION_IN_PROGRESS`, drawdown, etc.). | Revisar panel **Pre-Execution Gate**. Si `WS Reconciliation` esta ON, esperar a que termine. |
| `BLOCKED` | Un filtro veto la señal (volumen, ADX, RSI, shock, etc.). | No requiere accion; el filtro funciona como esperado. |
| `SELECTED` | La señal paso todos los filtros y fue seleccionada para PAPER/SHADOW. | Monitorear en la pestana Historial para ver el resultado. |
| `OBSERVED` | La señal fue analizada pero no ejecutada (WAIT o prob insuficiente). | No requiere accion. |

### Panel Pre-Execution Gate

| Flag | ON significa | Accion |
| :--- | :--- | :--- |
| `Daily/HALT` | HALT activo por drawdown o error critico. | Ver seccion 2 abajo. |
| `Integrity Lock` | Bloqueo de integridad por DB o estado inconsistente. | Ver seccion 3 abajo. |
| `Circuit Breaker` | Proteccion anti-panico disparada. | Ver seccion 2 abajo. |
| `Bot Paused` | Bot pausado manualmente o por guardian. | Usar `/resume` desde el dashboard si fue manual. |
| `WS Reconciliation` | El bot esta reconciliando estado tras una reconexion WS. | Esperar. Si dura > 30s, ver seccion 4 abajo. |

---

## 2. HALT o Circuit Breaker activo

1. Abrir el dashboard `http://127.0.0.1:8000`.
2. Verificar en el panel de Cuenta si `Estado` dice `HALT`.
3. Revisar `System Logs` para identificar el motivo (`DAILY_DRAWDOWN`, `CIRCUIT_BREAKER_PANIC`, `WS_RECONCILE_FAILED`, etc.).
4. Si el motivo es `DAILY_DRAWDOWN`:
   - No usar `/recover_halt` hasta revisar el balance real en el exchange.
   - Verificar `logs/runtime_metrics.jsonl` para detalles del PnL diario.
5. Si el motivo es `WS_RECONCILE_FAILED`:
   - Verificar conectividad del VPS al exchange.
   - Revisar `execution_events.jsonl` para ver el error exacto.
   - No forzar recover hasta que la conexion WebSocket sea estable.
6. Si el motivo es `CIRCUIT_BREAKER_PANIC`:
   - Revisar si hay posiciones abiertas sin HARD SL.
   - Verificar que `reconciliation.py` no haya detectado ordenes huerfanas.
7. Para recuperarse:
   - Asegurar que el motivo subyante esta resuelto.
   - Usar el boton `RECOVER HALT` en el dashboard o el comando `/recover_halt`.
   - Confirmar que `Estado` vuelve a `Activo`.

---

## 3. Integrity Lock activo

1. Revisar `System Logs` para el mensaje de integrity lock.
2. Posibles causas:
   - Error de escritura en la DB (`brain.db`).
   - Estado de ordenes/posiciones inconsistente.
   - Fallo en el startup migration (`features_version`).
3. Accion:
   - Si es DB: verificar espacio en disco y permisos del archivo `brain.db`.
   - Si es estado: reiniciar el bot y verificar `reconciliation.py` al arrancar.
   - Si es migracion: revisar `core/bot_models_startup.py` y el log de inicio.
4. Para liberar el lock:
   - Resolver la causa subyacente.
   - Reiniciar el bot si es necesario.

---

## 4. WS Reconciliation prolongada (> 30s)

1. Si el flag `WS Reconciliation` esta ON por mas de 30 segundos:
   - El sistema dispara automaticamente `WS_RECONCILE_TIMEOUT_ALERT`.
   - Se envia alerta a Telegram si esta configurado.
2. Accion:
   - Verificar la conexion a internet del VPS.
   - Verificar el estado de la API de Binance (`https://api.binance.com` o el endpoint configurado).
   - Revisar `tools/ws_manager.py` y `core/bot_io_loops.py` para ver si hay reconexiones repetidas.
   - Si el problema persiste, considerar reiniciar el bot.
3. En PAPER/SHADOW:
   - No hay impacto operativo real (no se ejecutan ordenes reales).
   - El flag es observacional y se limpia cuando la reconexion termina.
4. En REAL (futuro):
   - El bloqueo de entradas durante la reconciliacion es intencional.
   - No forzar el reset del flag manualmente.

---

## 5. NEUTRAL_AGENT_VOTE frecuente

1. Si la mayoria de rondas muestran `BLOCKED_NEUTRAL`:
   - Verificar si `ghost_model` esta cargado (`ghost_model_type != "OFF"`).
   - Si `bootstrap_heuristic_mode = True`, es esperado: no hay modelo ML entrenado.
   - Revisar `model_version` en el payload de consenso para confirmar.
2. Accion:
   - Si hay modelo pero sigue en neutral: revisar datos de entrada (OHLCV, features).
   - Si no hay modelo: entrenar o cargar un modelo `ghost_brain_advanced.pkl`.
   - Mientras tanto, el bot opera en modo heuristico y es seguro.

---

## 6. Dashboard no responde

1. Verificar que el proceso del bot esta activo:
   ```bash
   ps aux | grep main.py
   ```
2. Verificar el puerto 8000:
   ```bash
   ss -tlnp | grep 8000
   ```
3. Verificar `SNIPER_API_KEY` en `.env`.
4. Si el dashboard no inicia:
   - Revisar `uvicorn` en los logs.
   - Verificar que `SNIPER_DASHBOARD_AUTOSTART` no sea `false`.
5. Reiniciar el bot si es necesario.

---

## 7. Configuracion rapida

| Variable | Valor actual | Notas |
| :--- | :--- | :--- |
| `PAPER_MODE` | `true` | No cambiar a `false` sin auditoria completa. |
| `ALLOW_REAL_TRADING` | `false` | No cambiar sin validar este runbook. |
| `SHADOW_VALIDATION_ENABLED` | `true` | Campaigna SHADOW activa. |
| `WS_RECONCILE_TIMEOUT_SECONDS` | `30.0` | Timeout para alerta de reconciliacion. |
| `WS_RECONCILE_MIN_INTERVAL_SECONDS` | `30.0` | Debounce entre reconciliaciones. |

---

## 8. Comandos del dashboard

| Comando | Efecto | Seguro en PAPER? |
| :--- | :--- | :--- |
| `/pause` | Pausa el bot. | Si. |
| `/resume` | Reanuda el bot. | Si. |
| `/panic` | Activa HALT inmediato. | Si. |
| `/recover_halt` | Libera HALT y reinicia proteccion. | Si, pero verificar motivo primero. |

---

## 9. Logs y artefactos

| Archivo | Proposito | Rotacion |
| :--- | :--- | :--- |
| `logs/execution_events.jsonl` | Eventos estructurados de ejecucion. | 5 MB, 3 backups. |
| `logs/runtime_metrics.jsonl` | Metricas de runtime y campaigna SHADOW. | Automatica. |
| `/dev/shm/sniper_state.json` | Snapshot atomico para dashboard. | Cada 2s. |
| `brain.db` | DB principal de trades y contextos. | Backup diario. |

---

_Ultima actualizacion: 2026-07-08_
_Version runbook: v1_

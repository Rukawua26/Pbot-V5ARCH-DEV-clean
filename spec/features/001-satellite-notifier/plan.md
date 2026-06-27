# Plan: Satellite Notifier

## Prompt Engineering

- Rol: desarrollador Python de Pbot con foco en seguridad runtime y cambios minimos.
- Contexto: bot Binance Futures con modulos satelite read-only; FVG Tracker no debe tocar risk/sizing/ejecucion.
- Tarea: crear notifier callback simple e integrarlo opcionalmente al FVG Tracker.
- Restricciones: no tocar `core/bot_facade.py`, no tocar runtime critico, no activar FVG por defecto, no enviar notificaciones externas reales.
- Formato: tasks actualizadas, tests ejecutados, verify con evidencia anti-alucinacion.

## Enfoque Tecnico

- Extender `tools/notifier.py` con clase ligera `SatelliteNotifier` y tipo `NotificationCallback`.
- `SatelliteNotifier.notify(event, payload)` llamara callbacks registrados y capturara excepciones para no romper el flujo.
- Agregar un helper no-op por defecto o permitir `None` para no cambiar comportamiento existente.
- Adaptar `core/analytics/fvg_tracker.py` para aceptar notifier opcional y emitir evento cuando se registra un gap.
- Agregar tests unitarios enfocados.

## Archivos Afectados

- `tools/notifier.py` extender modulo existente sin romper Telegram.
- `core/analytics/fvg_tracker.py` modificar constructor y punto de registro de gap.
- `tests/test_notifier.py` nuevo.
- `tests/test_fvg_tracker_notifier.py` nuevo o test relacionado equivalente.

## Estructura De Datos

Evento recomendado:

```python
event = "fvg.gap_detected"
payload = {
    "symbol": str,
    "timeframe": str,
    "direction": str,
    "lower": float,
    "upper": float,
}
```

El payload final debe ajustarse a los campos reales del FVG Tracker.

## Decisiones De Implementacion

- Notifier local y sin dependencias externas.
- Excepciones de callbacks se capturan para mantener modulo observacional seguro.
- Integracion opcional para mantener compatibilidad.
- Tests con callbacks en memoria, sin I/O externo.

## Verificacion Contra Alucinaciones

- Confirmar campos reales del FVG Tracker antes de definir payload.
- Confirmar que `tools/notifier.py` ya existe y que la extension no rompe tests existentes de Telegram.
- Confirmar que `core/bot_facade.py` no fue modificado.
- Confirmar con tests que callback fallido no propaga excepcion.
- Confirmar con test que FVG Tracker sin notifier no falla.

## Loop Auto-Correctivo

1. Implementar cambio minimo.
2. Ejecutar test enfocado.
3. Si falla, corregir dentro del alcance.
4. Ejecutar validaciones adicionales relevantes.
5. Registrar evidencia en `verify.md`.

## Estrategia De Testing

- Test unitario de `Notifier` sin callbacks, con multiples callbacks y con callback que falla.
- Test unitario de FVG Tracker verificando emision de evento cuando registra gap.
- Ejecutar tests enfocados con `SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest ...`.
- Si viable, ejecutar `compileall` sobre `core` y `tools`.

## Riesgos Tecnicos

- FVG Tracker puede tener API distinta a la esperada; adaptar despues de leer archivo real.
- `tools` puede no tener `__init__.py`; usar import compatible con unittest desde repo root.

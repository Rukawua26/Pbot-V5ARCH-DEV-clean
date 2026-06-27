# Spec: Satellite Notifier

## Objetivo

Crear un mecanismo pequeño de notificacion por callback para modulos satelite, empezando por FVG Tracker, sin ampliar `core/bot_facade.py` ni acoplar modulos observacionales al runtime critico.

## Usuario / Caso De Uso

Como mantenedor de Pbot, quiero que modulos satelite read-only puedan emitir eventos observacionales mediante un callback inyectable para que futuras integraciones reporten informacion sin tocar contratos publicos del bot ni rutas de ejecucion.

## Alcance

- Extender `tools/notifier.py` con una interfaz simple para callbacks satelite.
- Permitir que FVG Tracker reciba un notifier opcional.
- Emitir eventos observacionales del FVG Tracker por callback cuando registre gaps.
- Agregar tests enfocados para notifier y FVG Tracker.
- Mantener el notifier sin efectos colaterales si no hay callbacks configurados.

## Fuera De Alcance

- No tocar `core/bot_facade.py`.
- No tocar ejecucion, sizing, risk engine, entradas, salidas, wallet sync, reconciliacion ni `REAL`.
- No activar FVG Tracker por defecto.
- No enviar mensajes reales a Telegram, dashboards o servicios externos.
- No integrar FVG al Risk Engine.

## Limites De Contexto

- Leer solo: `docs/engineering/memoria-tecnica.md`, `docs/roadmap/mejoras-pendientes.md`, `core/analytics/fvg_tracker.py`, tests relacionados, y archivos necesarios para imports de tests.
- No leer: `core/execution_*`, `core/bot_facade.py`, `core/trade_entry.py`, `core/trade_exit.py`, bases de datos, logs, reports ni `node_modules`.
- Si la implementacion requiere tocar runtime critico, detenerse y actualizar esta spec antes de continuar.

## Fuentes De Verdad

- `docs/engineering/memoria-tecnica.md`: regla preventiva sobre no ampliar `core/bot_facade.py`; usar callback inyectado o `tools.notifier`.
- `docs/roadmap/mejoras-pendientes.md`: FVG Tracker debe seguir como modulo satelite read-only y no integrarse a risk/sizing/ejecucion sin evidencia.
- `AGENTS.md`: invariantes operativos, comandos de validacion y limites.
- Tests nuevos y existentes bajo `tests/`.

## Criterios De Aceptacion

- [ ] `tools/notifier.py` conserva API Telegram existente y agrega API simple para callbacks.
- [ ] FVG Tracker acepta notifier opcional sin romper construccion actual.
- [ ] FVG Tracker emite evento observacional cuando detecta/registra un gap.
- [ ] Si no hay notifier, FVG Tracker mantiene comportamiento actual.
- [ ] Si un callback falla, no rompe el tracker ni el runtime.
- [ ] No se modifica `core/bot_facade.py` ni runtime critico.
- [ ] Tests enfocados cubren notifier y emision de evento FVG.

## Casos De Borde

- Notifier sin callbacks.
- Callback que lanza excepcion.
- Multiples callbacks registrados.
- Tracker sin detecciones.
- Tracker con deteccion que registra gap.

## Riesgos

- Introducir efectos colaterales en modulo observacional.
- Acoplar FVG Tracker a herramientas externas de notificacion.
- Romper imports si `tools` no se comporta como paquete.

## Preguntas Abiertas

- Ninguna bloqueante.

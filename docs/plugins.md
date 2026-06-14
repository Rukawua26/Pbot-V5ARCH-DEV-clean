# Documentación de Plugins — Sniper AI

## Resumen

Sniper AI no implementa un sistema de plugins propio. El único "plugin" en el repositorio es la dependencia npm **`@opencode-ai/plugin`** que provee la integración con OpenCode (el asistente CLI usado para desarrollo).

Este documento describe:
- Qué es el plugin de OpenCode
- Cómo afecta (o no) al bot
- Dónde se configura
- Cómo crear agentes/skills personalizados en `.opencode/`

---

## `@opencode-ai/plugin`

| Propiedad | Valor |
|-----------|-------|
| Paquete npm | `@opencode-ai/plugin@1.15.13` |
| Propósito | Integración CLI de OpenCode (agentes, skills, comandos, MCP, permisos) |
| Ubicación | `.opencode/package.json` + `.opencode/package-lock.json` |
| Impacto en el bot | **Ninguno directo** — es herramienta de desarrollo, no runtime |

El plugin de OpenCode **no se ejecuta** cuando el bot corre en `PAPER`, `SHADOW` o `REAL`. Solo está presente en el entorno de desarrollo.

---

## Estructura `.opencode/`

```
.opencode/
├── agents/          # Agentes especializados (security-reviewer, repo-explorer, etc.)
├── commands/        # Comandos personalizados (slash commands)
├── skills/          # Skills curadas (runtime-ops, security, python-testing, etc.)
├── context/         # Contexto persistente (repo-summary, critical-runtime-rules, etc.)
├── package.json     # Dependencia del plugin OpenCode
└── opencode.json    # Configuración principal (si existe)
```

### Agentes disponibles
Ver `.opencode/agents/` — cada archivo `.md` define un agente con su prompt y herramientas.

### Skills curadas
Ver `.opencode/skills/` — skills que se cargan bajo demanda:
- `runtime-ops-and-trading-safety` — execution, Binance Futures, HALT, SL, wallet sync
- `security-and-hardening` — secrets, .env, API keys, auth, network
- `repo-validation` — validación CI, smoke tests, regression contracts
- `python-testing` — unittest, fixtures, mocks, temporal invariance
- `opencode-customization` — configuración del propio OpenCode

### Contexto persistente
Ver `.opencode/context/` — referencia rápida para agentes:
- `repo-summary.md` — arquitectura clave
- `critical-runtime-rules.md` — invariantes operativos
- `validation-commands.md` — comandos de validación
- `skill-policy.md` — cuándo cargar cada skill
- `known-bugs.md` — prevención de regresiones

---

## Cómo agregar agentes/skills propios

1. **Nuevo agente**: Crear `.opencode/agents/mi-agente.md` siguiendo el formato de los existentes
2. **Nueva skill**: Crear `.opencode/skills/mi-skill/SKILL.md` + scripts si aplica
3. **Registrar en opencode.json**: Si existe, añadir al array `skills` o `agents`

> **Nota**: No hay archivo `opencode.json` en la raíz del proyecto. OpenCode usa configuración por defecto + `.opencode/`.

---

## Configuración del plugin

El plugin se instala via npm en `.opencode/`:

```bash
cd .opencode && npm install @opencode-ai/plugin@1.15.13
```

No requiere configuración adicional para funcionar. Los agentes/skills se descubren automáticamente desde `.opencode/`.

---

## Seguridad

Ver `.opencode/skills/security-and-hardening/SKILL.md` — el plugin npm es código de terceros. Revisar:
- `package-lock.json` para versiones fijas
- Sin dependencias con vulnerabilidades conocidas (`npm audit`)
- Permisos del directorio `.opencode/` (debe ser 700, archivos 600)

---

## Preguntas frecuentes

**¿Puedo hacer que el bot cargue plugins dinámicamente?**
No. El bot no tiene sistema de plugins. Para extender funcionalidad, modificar `core/` directamente o usar el sistema de estrategias/agentes en `core/strategy/agents/`.

**¿El plugin afecta al dashboard?**
No. El dashboard (`dashboard/api_server.py`, `tools/dashboard.py`) es independiente.

**¿Cómo actualizar el plugin?**
```bash
cd .opencode && npm update @opencode-ai/plugin
```
Verificar que no rompe agentes/skills existentes corriendo validación.

---

## Referencias

- OpenCode docs: https://opencode.ai
- Skills curadas: `.opencode/skills/README.md`
- Contexto del repo: `.opencode/context/repo-summary.md`
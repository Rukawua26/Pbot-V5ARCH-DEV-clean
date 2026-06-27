#!/usr/bin/env python3
"""Read-only readiness report for pending PAPER/SHADOW improvements."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(
    env: dict[str, str], key: str, default: str = "", *, include_os_env: bool = True
) -> str:
    if key in env:
        return str(env[key])
    if include_os_env:
        return str(os.getenv(key, default))
    return default


def _env_bool(
    env: dict[str, str], key: str, default: bool, *, include_os_env: bool = True
) -> bool:
    raw = _env_value(
        env, key, "true" if default else "false", include_os_env=include_os_env
    ).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def build_report(
    env: dict[str, str], *, include_os_env: bool = True
) -> tuple[list[str], list[str], list[str]]:
    ok: list[str] = []
    warnings: list[str] = []
    blocked: list[str] = []

    paper_mode = _env_bool(env, "PAPER_MODE", True, include_os_env=include_os_env)
    allow_real = _env_bool(env, "ALLOW_REAL_TRADING", False, include_os_env=include_os_env)
    execution_backend = _env_value(
        env, "EXECUTION_BACKEND", "live", include_os_env=include_os_env
    )
    fvg_enabled = _env_bool(env, "FVG_TRACKER_ENABLED", False, include_os_env=include_os_env)
    global_market_enabled = _env_bool(
        env, "GLOBAL_MARKET_PROVIDER_ENABLED", False, include_os_env=include_os_env
    )
    fear_filter_enabled = _env_bool(
        env, "GLOBAL_FEAR_GREED_FILTER_ENABLED", True, include_os_env=include_os_env
    )
    btc_dom_filter_enabled = _env_bool(
        env, "GLOBAL_BTC_DOM_FILTER_ENABLED", True, include_os_env=include_os_env
    )
    override_enabled = _env_bool(
        env, "SIGNAL_AGENT_OVERRIDE_ENABLED", True, include_os_env=include_os_env
    )
    api_key = _env_value(env, "SNIPER_API_KEY", "", include_os_env=include_os_env)

    if paper_mode:
        ok.append("Modo PAPER activo: seguro para observacion sin capital real.")
    elif execution_backend == "shadow_live" and not allow_real:
        ok.append("Modo SHADOW detectado por EXECUTION_BACKEND=shadow_live sin ALLOW_REAL_TRADING.")
    else:
        blocked.append(
            "La observacion no debe ejecutarse en REAL: usa PAPER_MODE=true o SHADOW sin ALLOW_REAL_TRADING."
        )

    if fvg_enabled:
        ok.append("FVG Tracker activado para medir gaps en PAPER/SHADOW.")
    else:
        warnings.append("FVG_TRACKER_ENABLED=false: no se recolectaran metricas FVG.")

    if global_market_enabled:
        ok.append("Global Market Provider activado para contexto macro read-only.")
    else:
        warnings.append("GLOBAL_MARKET_PROVIDER_ENABLED=false: filtros macro usaran defaults sin datos macro reales.")

    if fear_filter_enabled or btc_dom_filter_enabled:
        ok.append("Filtros macro configurados; valida thresholds solo con evidencia PAPER/SHADOW.")
    else:
        warnings.append("Filtros macro desactivados: no habra veto/boost por Fear & Greed ni BTC dominance.")

    if override_enabled:
        ok.append("Consensus direction override activado; requiere comparar trades SHADOW contra baseline.")
    else:
        warnings.append("SIGNAL_AGENT_OVERRIDE_ENABLED=false: no se observara direccion por consenso.")

    if len(api_key) >= 16:
        ok.append("SNIPER_API_KEY valida para Dashboard API.")
    else:
        warnings.append("SNIPER_API_KEY ausente o corta: Dashboard API no esta listo.")

    return ok, warnings, blocked


def render_report(ok: list[str], warnings: list[str], blocked: list[str]) -> str:
    lines = ["# Pending Improvements Readiness", ""]
    if blocked:
        lines.append("## Bloqueos")
        lines.extend(f"- {item}" for item in blocked)
        lines.append("")
    lines.append("## OK")
    lines.extend(f"- {item}" for item in ok)
    lines.append("")
    lines.append("## Observaciones")
    lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


def main() -> int:
    env = _load_dotenv(PROJECT_ROOT / ".env")
    ok, warnings, blocked = build_report(env)
    print(render_report(ok, warnings, blocked))
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())

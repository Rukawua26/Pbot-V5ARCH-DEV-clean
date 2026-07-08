from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import requests


def obtener_ip_publica() -> str:
    try:
        response = requests.get("https://api.ipify.org?format=json", timeout=4.0)
        response.raise_for_status()
        return str(response.json().get("ip") or "No detectada")
    except Exception:
        return "No detectada (verifica tu conexión/firewall)"


def _clear_screen() -> None:
    os.system("cls" if sys.platform == "win32" else "clear")


def ejecutar_multi_wizard(base_dir: Path, env_path: Path) -> None:
    for folder in ("data", "logs", "models", "backups"):
        (base_dir / folder).mkdir(parents=True, exist_ok=True)

    _clear_screen()
    print("=" * 72)
    print("      SNIPER AI QUANT RUNTIME - INITIAL PORTABLE SETUP")
    print("=" * 72)
    ip_publica = obtener_ip_publica()
    print("\n[!] CONTROL DE RESTRICCIÓN DE IP (BINANCE)")
    print(f"    IP pública detectada: {ip_publica}")
    print("    Si usas API keys con whitelist, agrega esta IP en Binance.")
    print("\n[SEGURIDAD] La versión portable arranca SIEMPRE en PAPER.")
    print("    REAL trading queda deshabilitado por defecto.")
    print("=" * 72)

    api_key = input("\n1. Binance Futures API Key (opcional para PAPER público): ").strip()
    api_secret = getpass.getpass("2. Binance Futures API Secret (oculto): ").strip()
    dashboard_key = getpass.getpass(
        "3. Dashboard read key (opcional, Enter para generar local): "
    ).strip()
    if not dashboard_key:
        import secrets

        dashboard_key = secrets.token_hex(32)

    template_env = f"""# AUTO-GENERATED CONFIGURATION FOR SNIPER AI PORTABLE
PAPER_MODE=true
ALLOW_REAL_TRADING=false
EXECUTION_BACKEND=live
BINANCE_API_KEY={api_key}
BINANCE_API_SECRET={api_secret}
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000
SNIPER_API_KEY={dashboard_key}
SNIPER_DASHBOARD_AUTOSTART=1
SNIPER_INTELLIGENCE_AUTOSTART=1
SNIPER_DB_PATH={base_dir / "data" / "sniper_brain.db"}
SNIPER_LOG_DIR={base_dir / "logs"}
SNIPER_MODEL_DIR={base_dir / "models"}
TOP_TRIAGE_COUNT=30
TRIAGE_CANDIDATE_POOL_MULTIPLIER=1
TRIAGE_MAX_CANDIDATE_POOL=30
"""
    env_path.write_text(template_env, encoding="utf-8")

    print("\n[OK] Configuración guardada.")
    print(f"     Ruta persistente: {base_dir}")
    print(f"     Archivo .env: {env_path}")
    print("     Iniciando bot en PAPER...\n")

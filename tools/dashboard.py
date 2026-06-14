"""
SNIPER AI - DASHBOARD MODULE
===========================
Canonical implementation for the bot's web interface and IPC.
"""
import importlib
import os
import socket
import threading
import time
from dataclasses import dataclass

_dashboard_lock = threading.Lock()
_dashboard_thread: threading.Thread | None = None

@dataclass(frozen=True)
class DashboardHandle:
    host: str
    port: int
    thread: threading.Thread | None
    already_running: bool = False
    enabled: bool = True


def __getattr__(name: str):
    if name == "api_server":
        # Import lazily so bootstrap/smoke imports do not require SNIPER_API_KEY.
        return importlib.import_module("tools.dashboard_api_server")
    raise AttributeError(name)

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None: return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0

def _log(bot, message: str) -> None:
    logger = getattr(bot, "log", None)
    if callable(logger): logger(message)

def start_dashboard(bot=None) -> DashboardHandle:
    """Start the canonical FastAPI dashboard."""
    enabled = _env_bool("SNIPER_DASHBOARD_AUTOSTART", True)
    host = os.getenv("SNIPER_DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("SNIPER_DASHBOARD_PORT", "8000"))

    if host == "0.0.0.0":
        expose_enabled = _env_bool("SNIPER_DASHBOARD_EXPOSE", False)
        if not expose_enabled:
            _log(bot, "🚨 SECURITY WARNING: SNIPER_DASHBOARD_HOST=0.0.0.0 requiere SNIPER_DASHBOARD_EXPOSE=1 para exponer el dashboard.")
            return DashboardHandle(host="127.0.0.1", port=port, thread=None, enabled=False)

    if not enabled:
        _log(bot, "🖥️ Dashboard localhost deshabilitado por SNIPER_DASHBOARD_AUTOSTART.")
        return DashboardHandle(host=host, port=port, thread=None, enabled=False)

    with _dashboard_lock:
        global _dashboard_thread
        if _dashboard_thread and _dashboard_thread.is_alive():
            return DashboardHandle(host=host, port=port, thread=_dashboard_thread)

        if _is_port_open(host, port):
            _log(bot, f"🖥️ Dashboard localhost ya disponible en http://{host}:{port}")
            return DashboardHandle(host=host, port=port, thread=None, already_running=True)

        try:
            import uvicorn
        except ImportError as error:
            _log(bot, f"⚠️ Dashboard localhost no disponible: uvicorn no instalado ({error})")
            return DashboardHandle(host=host, port=port, thread=None, enabled=False)

        def _run_server() -> None:
            config = uvicorn.Config(
                "tools.dashboard_api_server:app", # Apunta al nuevo módulo canónico
                host=host,
                port=port,
                log_level=os.getenv("SNIPER_DASHBOARD_LOG_LEVEL", "warning"),
                access_log=False,
            )
            server = uvicorn.Server(config)
            server.run()

        _dashboard_thread = threading.Thread(
            target=_run_server,
            name="sniper-dashboard-localhost",
            daemon=True,
        )
        _dashboard_thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if _is_port_open(host, port):
            _log(bot, f"🖥️ Dashboard localhost disponible en http://{host}:{port}")
            break
        time.sleep(0.1)
    else:
        _log(bot, f"⚠️ Dashboard localhost arrancando lento en http://{host}:{port}")

    return DashboardHandle(host=host, port=port, thread=_dashboard_thread)

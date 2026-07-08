from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "SniperBot"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_portable_runtime() -> bool:
    return is_frozen() or bool(os.getenv("SNIPER_PORTABLE_BASE_DIR"))


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def portable_base_dir() -> Path:
    override = os.getenv("SNIPER_PORTABLE_BASE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if not is_frozen():
        return project_root()
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def env_path(base_dir: Path | None = None) -> Path:
    return (base_dir or portable_base_dir()) / ".env"


def data_dir(base_dir: Path | None = None) -> Path:
    if not is_portable_runtime() and base_dir is None:
        return Path("data")
    return (base_dir or portable_base_dir()) / "data"


def logs_dir(base_dir: Path | None = None) -> Path:
    if not is_portable_runtime() and base_dir is None:
        return Path("logs")
    return (base_dir or portable_base_dir()) / "logs"


def models_dir(base_dir: Path | None = None) -> Path:
    if not is_portable_runtime() and base_dir is None:
        return Path("models")
    return (base_dir or portable_base_dir()) / "models"


def backups_dir(base_dir: Path | None = None) -> Path:
    if not is_portable_runtime() and base_dir is None:
        return Path("backups")
    return (base_dir or portable_base_dir()) / "backups"


def runtime_path(*parts: str, base_dir: Path | None = None) -> Path:
    return (base_dir or portable_base_dir()).joinpath(*parts)


def ensure_portable_dirs(base_dir: Path | None = None) -> Path:
    base = base_dir or portable_base_dir()
    for folder in (base, data_dir(base), logs_dir(base), models_dir(base), backups_dir(base)):
        folder.mkdir(parents=True, exist_ok=True)
    return base


def configure_runtime_environment(base_dir: Path | None = None) -> Path:
    if is_portable_runtime():
        base = ensure_portable_dirs(base_dir)
        os.environ.setdefault("SNIPER_DATA_DIR", str(data_dir(base)))
        os.environ.setdefault("SNIPER_LOG_DIR", str(logs_dir(base)))
        os.environ.setdefault("SNIPER_MODEL_DIR", str(models_dir(base)))
        os.environ.setdefault("SNIPER_BACKUP_DIR", str(backups_dir(base)))
        os.environ.setdefault("SNIPER_DB_PATH", str(data_dir(base) / "sniper_brain.db"))
        return base
    base = base_dir or portable_base_dir()
    return base


def load_runtime_env() -> Path:
    base = configure_runtime_environment()
    env_file = env_path(base)
    if is_frozen() and not env_file.exists():
        from core.config.wizard import ejecutar_multi_wizard

        ejecutar_multi_wizard(base, env_file)
    load_dotenv(env_file, override=True)
    return base


def get_log_path(filename: str) -> Path:
    return logs_dir() / filename


def get_model_path(filename: str) -> Path:
    return models_dir() / filename

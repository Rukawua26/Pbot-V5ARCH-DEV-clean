import os
from pathlib import Path

from core.config.portable_paths import data_dir, is_portable_runtime


def resolve_default_db_path() -> str:
    env_db_path = os.getenv("SNIPER_DB_PATH")
    if env_db_path:
        return env_db_path
    if is_portable_runtime():
        return str(data_dir() / "sniper_brain.db")
    return str(Path(__file__).resolve().parent.parent / "tools" / "sniper_brain.db")


DEFAULT_DB_PATH = resolve_default_db_path()

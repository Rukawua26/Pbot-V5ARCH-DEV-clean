import os

_CONFIG_ENV_WARNINGS: list[str] = []


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
        return default


def env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


def env_list(name: str, default: list | None = None) -> list:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default if default is not None else []
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
    return default


def get_env_warnings() -> list[str]:
    return list(_CONFIG_ENV_WARNINGS)

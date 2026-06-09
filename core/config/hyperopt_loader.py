from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("SniperAI")


class HyperoptConfigLoader:
    """Cargador centralizado de hiperparámetros optimizados.

    Lee `config_hyperopt.json` desde la raíz del proyecto.
    Mantiene caché en memoria y aplica defaults seguros si falta el archivo.
    """

    _cache: dict[str, Any] | None = None
    _path = Path(__file__).resolve().parents[2] / "config_hyperopt.json"

    @classmethod
    def _defaults(cls) -> dict[str, Any]:
        return {
            "enabled": False,
            "timeframe": "1h",
            "params": {
                "alma_offset": 0.85,
                "alma_sigma": 6.0,
                "z_score_threshold": 2.5,
                "entropy_bins": 10,
                "adx_threshold": 25.0,
                "stop_loss_pct": 2.45,
                "take_profit_pct": 6.47,
            },
            "symbols": {},
        }

    @staticmethod
    def _normalize_symbol(symbol: str | None) -> str:
        return str(symbol or "").upper().split(":")[0]

    @classmethod
    def reload(cls) -> dict[str, Any]:
        cls._cache = None
        return cls.get_config()

    @classmethod
    def get_config(cls) -> dict[str, Any]:
        if cls._cache is not None:
            return cls._cache

        cfg = cls._defaults()
        if cls._path.exists():
            try:
                loaded = json.loads(cls._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update({k: v for k, v in loaded.items() if k != "params"})
                    loaded_params = loaded.get("params", {})
                    if isinstance(loaded_params, dict):
                        cfg["params"].update(loaded_params)
                    loaded_symbols = loaded.get("symbols", {})
                    if isinstance(loaded_symbols, dict):
                        cfg["symbols"] = loaded_symbols
                logger.info(f"✅ Hyperopt config cargado desde {cls._path}")
            except Exception as e:
                logger.warning(f"⚠️ Error leyendo {cls._path}: {e}. Usando defaults.")
        else:
            logger.info("ℹ️ config_hyperopt.json no existe. Usando defaults.")

        cls._cache = cfg
        return cfg

    @classmethod
    def get_param(cls, key: str, default: Any = None) -> Any:
        cfg = cls.get_config()
        return cfg.get("params", {}).get(key, default)

    @classmethod
    def get_params_for_symbol(cls, symbol: str | None) -> dict[str, Any]:
        cfg = cls.get_config()
        params = dict(cfg.get("params", {}) or {})
        normalized = cls._normalize_symbol(symbol)
        symbols = cfg.get("symbols", {}) or {}
        symbol_params = symbols.get(normalized) or symbols.get(normalized.replace("/", "_"))
        if isinstance(symbol_params, dict):
            params.update(symbol_params)
        return params

    @classmethod
    def get_param_for_symbol(cls, symbol: str | None, key: str, default: Any = None) -> Any:
        return cls.get_params_for_symbol(symbol).get(key, default)

    @classmethod
    def is_enabled(cls) -> bool:
        cfg = cls.get_config()
        return bool(cfg.get("enabled", False))

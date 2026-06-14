"""
SNIPER AI v118.3 — OI Delta Filter
===================================
Filtro externo rígido de Open Interest.
Detecta señales falsas (short squeeze, long liquidation) comparando
la dirección del precio con el cambio en Open Interest.

Reglas:
  BUY  + precio↑ + OI↑ (>threshold)  → CONFIRMED (dinero nuevo apoyando subida)
  BUY  + precio↑ + OI↓ (<-threshold) → VETO (short squeeze, subida falsa)
  SELL + precio↓ + OI↑ (>threshold)  → CONFIRMED (dinero nuevo apoyando caída)
  SELL + precio↓ + OI↓ (<-threshold) → VETO (long liquidation, caída falsa)
  Todo lo demás                       → NEUTRAL (sin datos suficientes)
"""

import logging
import time

from config import Config

logger = logging.getLogger("SniperAI")

# Cache interno: {symbol: {"oi": float, "previous_oi": float | None, "ts": float}}
_oi_cache: dict = {}


def _get_cached_oi(symbol: str, ttl_multiplier: float = 1.0) -> float | None:
    """Retorna el OI anterior cacheado si no ha expirado.

    Args:
        symbol: Símbolo a consultar.
        ttl_multiplier: Factor multiplicador sobre OI_CACHE_TTL_SECONDS
                        (3.0 = referencia histórica, 1.0 = API-level TTL).
    """
    entry = _oi_cache.get(symbol)
    if not entry:
        return None
    ttl = float(getattr(Config, "OI_CACHE_TTL_SECONDS", 60)) * ttl_multiplier
    if time.time() - entry["ts"] > ttl:
        return None
    return entry["oi"]


def _get_previous_cached_oi(symbol: str, ttl_multiplier: float = 3.0) -> float | None:
    entry = _oi_cache.get(symbol)
    if not entry:
        return None
    ttl = float(getattr(Config, "OI_CACHE_TTL_SECONDS", 60)) * ttl_multiplier
    if time.time() - entry["ts"] > ttl:
        return None
    previous = entry.get("previous_oi")
    return float(previous) if previous is not None else None


def _update_oi_cache(symbol: str, oi_value: float):
    """Actualiza el cache con el OI actual."""
    previous = _oi_cache.get(symbol, {}).get("oi")
    _oi_cache[symbol] = {"oi": oi_value, "previous_oi": previous, "ts": time.time()}


def fetch_oi_delta(bot, symbol: str) -> tuple[float | None, float | None]:
    """
    Obtiene el OI actual y calcula el delta contra el valor cacheado.
    Usa cache TTL para evitar llamadas API redundantes.

    Returns:
        (oi_delta_pct, oi_current) — delta como fracción (0.01 = 1%), o (None, None)
    """
    try:
        execution = getattr(bot, "execution", None)
        if execution is None:
            return None, None

        # Verificar rate limiter antes de hacer la llamada
        weight_tracker = getattr(bot, "weight_tracker", None)
        if weight_tracker and weight_tracker.should_block("market"):
            return None, None

        # API-level TTL cache: evitar fetch si ya tenemos OI reciente
        oi_cached = _get_cached_oi(symbol, ttl_multiplier=1.0)
        if oi_cached is not None:
            oi_previous = _get_previous_cached_oi(symbol, ttl_multiplier=3.0)
            if oi_previous is None or oi_previous <= 0:
                return None, oi_cached
            oi_delta_pct = (oi_cached - oi_previous) / oi_previous
            return oi_delta_pct, oi_cached

        oi_response = execution.fetch_open_interest(symbol)
        if not isinstance(oi_response, dict):
            return None, None

        oi_current = float(oi_response.get("openInterestAmount", 0) or 0)
        if oi_current <= 0:
            return None, None

        oi_previous = _get_cached_oi(symbol, ttl_multiplier=3.0)
        _update_oi_cache(symbol, oi_current)

        if oi_previous is None or oi_previous <= 0:
            return None, oi_current

        oi_delta_pct = (oi_current - oi_previous) / oi_previous
        return oi_delta_pct, oi_current

    except Exception as e:
        logger.warning(f"⚠️ OI delta calc falló para {symbol}: {e}")
        return None, None


def validate_signal_with_oi(
    audit_signal: str, delta_price_pct: float, oi_delta_pct: float | None
) -> str:
    """
    Valida la señal contra el cambio de OI.

    Args:
        audit_signal: "BUY" o "SELL"
        delta_price_pct: cambio de precio reciente como fracción (0.01 = 1%)
        oi_delta_pct: cambio de OI como fracción, o None si no hay dato

    Returns:
        "CONFIRMED" | "VETO" | "NEUTRAL"
    """
    if oi_delta_pct is None:
        return "NEUTRAL"

    threshold = float(getattr(Config, "OI_DELTA_THRESHOLD", 0.005))

    if audit_signal == "BUY":
        if delta_price_pct > 0 and oi_delta_pct > threshold:
            return "CONFIRMED"  # Dinero nuevo apoyando la subida
        elif delta_price_pct > 0 and oi_delta_pct < -threshold:
            return "VETO"  # Short squeeze — subida falsa
    elif audit_signal == "SELL":
        if delta_price_pct < 0 and oi_delta_pct > threshold:
            return "CONFIRMED"  # Dinero nuevo apoyando la caída
        elif delta_price_pct < 0 and oi_delta_pct < -threshold:
            return "VETO"  # Long liquidation — caída falsa

    return "NEUTRAL"

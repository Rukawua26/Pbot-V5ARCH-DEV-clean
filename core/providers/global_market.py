import logging
import time
from typing import Any

import requests

from config import Config

logger = logging.getLogger("SniperAI")


class GlobalMarketProvider:
    """Satellite provider for global crypto market metrics (CoinGecko REST).

    Read-only, fail-silent, never blocks runtime.
    Cache interno con TTL configurable.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._enabled: bool = False
        self._last_error_log: float = 0.0
        self._error_log_interval: float = 300.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        self._enabled = bool(getattr(Config, "GLOBAL_MARKET_PROVIDER_ENABLED", False))
        if self._enabled:
            logger.info("GlobalMarketProvider activado (CoinGecko REST)")
        else:
            logger.info("GlobalMarketProvider desactivado (GLOBAL_MARKET_PROVIDER_ENABLED=false)")

    def stop(self) -> None:
        self._enabled = False

    def fetch_global_metrics(self) -> dict[str, Any]:
        """Return cached or fresh global market metrics.

        Returns empty dict if disabled, on error, or if cache is fresh.
        Never raises.
        """
        if not self._enabled:
            return {}

        ttl = int(getattr(Config, "GLOBAL_MARKET_CACHE_TTL", 300))
        now = time.time()
        if self._cache and (now - self._cache_ts) < ttl:
            return self._cache

        try:
            data = self._fetch_from_coingecko()
            if data:
                self._cache = data
                self._cache_ts = now
            return self._cache if self._cache else {}
        except Exception as exc:
            if now - self._last_error_log > self._error_log_interval:
                logger.warning("GlobalMarketProvider error: %s", exc)
                self._last_error_log = now
            return self._cache if self._cache else {}

    def _fetch_from_coingecko(self) -> dict[str, Any] | None:
        use_mcp = bool(getattr(Config, "GLOBAL_MARKET_USE_MCP", False))
        if use_mcp:
            return self._fetch_from_mcp()
        return self._fetch_from_rest()

    def _fetch_from_rest(self) -> dict[str, Any] | None:
        timeout = 5.0
        result: dict[str, Any] = {}

        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=timeout,
            )
            resp.raise_for_status()
            gd = resp.json().get("data", {})
            if gd:
                mcp = gd.get("market_cap_percentage", {}) or {}
                result["btc_dominance"] = float(mcp.get("btc", 0.0) or 0.0)
                result["eth_dominance"] = float(mcp.get("eth", 0.0) or 0.0)
                tmc = gd.get("total_market_cap", {}) or {}
                result["total_market_cap"] = float(tmc.get("usd", 0.0) or 0.0)
                tv = gd.get("total_volume", {}) or {}
                result["total_volume_24h"] = float(tv.get("usd", 0.0) or 0.0)
                result["active_cryptos"] = int(gd.get("active_cryptocurrencies", 0) or 0)
        except Exception as exc:
            logger.warning("CoinGecko /global error: %s", exc)

        try:
            tresp = requests.get(
                "https://api.coingecko.com/api/v3/trending",
                timeout=timeout,
            )
            tresp.raise_for_status()
            coins = tresp.json().get("coins", []) or []
            result["trending_coins"] = [
                c.get("item", {}).get("id", "") for c in coins if c.get("item", {}).get("id")
            ][:15]
        except Exception:
            None

        try:
            fresp = requests.get(
                "https://api.coingecko.com/api/v3/fear_greed",
                timeout=timeout,
            )
            fresp.raise_for_status()
            fg_data = fresp.json()
            if fg_data.get("data"):
                result["fear_greed"] = float(fg_data["data"][0].get("value", 50))
        except Exception:
            None

        return result if result else None

    def _fetch_from_mcp(self) -> dict[str, Any] | None:
        logger.warning("MCP mode not yet implemented - falling back to REST")
        return self._fetch_from_rest()

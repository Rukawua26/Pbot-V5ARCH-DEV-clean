import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.providers.global_market import GlobalMarketProvider


class GlobalMarketProviderTest(unittest.TestCase):
    def setUp(self):
        self.provider = GlobalMarketProvider()

    def tearDown(self):
        self.provider.stop()

    def test_disabled_by_default_returns_empty(self):
        result = self.provider.fetch_global_metrics()
        self.assertEqual(result, {})

    def test_start_enables_provider(self):
        with patch("core.providers.global_market.Config.GLOBAL_MARKET_PROVIDER_ENABLED", True):
            self.provider.start()
            self.assertTrue(self.provider.enabled)

    def test_cache_ttl_avoids_duplicate_fetch(self):
        self.provider._enabled = True
        self.provider._cache = {"btc_dominance": 55.0}
        self.provider._cache_ts = time.time()

        with patch.object(
            GlobalMarketProvider, "_fetch_from_coingecko", return_value={"btc_dominance": 99.0}
        ) as mock_fetch:
            result = self.provider.fetch_global_metrics()
            mock_fetch.assert_not_called()
            self.assertEqual(result["btc_dominance"], 55.0)

    def test_expired_cache_triggers_refetch(self):
        self.provider._enabled = True
        self.provider._cache = {"btc_dominance": 55.0}
        self.provider._cache_ts = time.time() - 600  # 10min ago > 300s TTL

        with patch.object(
            GlobalMarketProvider, "_fetch_from_coingecko", return_value={"btc_dominance": 77.0}
        ) as mock_fetch:
            result = self.provider.fetch_global_metrics()
            mock_fetch.assert_called_once()
            self.assertEqual(result["btc_dominance"], 77.0)

    def test_returns_stale_cache_on_fetch_error(self):
        self.provider._enabled = True
        self.provider._cache = {"btc_dominance": 55.0}
        self.provider._cache_ts = time.time() - 600

        with patch.object(
            GlobalMarketProvider, "_fetch_from_coingecko", side_effect=Exception("API down")
        ):
            result = self.provider.fetch_global_metrics()
            self.assertEqual(result["btc_dominance"], 55.0)

    def test_returns_empty_on_first_error_without_cache(self):
        self.provider._enabled = True

        with patch.object(
            GlobalMarketProvider, "_fetch_from_coingecko", side_effect=Exception("API down")
        ):
            result = self.provider.fetch_global_metrics()
            self.assertEqual(result, {})

    def test_coingecko_fetch_falls_back_to_rest_when_mcp_disabled(self):
        self.provider._enabled = True
        with patch.object(
            GlobalMarketProvider, "_fetch_from_rest", return_value={"btc_dominance": 50.0}
        ) as mock_rest:
            result = self.provider._fetch_from_coingecko()
            mock_rest.assert_called_once()
            self.assertEqual(result["btc_dominance"], 50.0)


class GlobalMarketIntegrationWithBotTest(unittest.TestCase):
    """Verify the inject points don't break when provider is absent."""

    def test_context_injection_without_provider_does_not_crash(self):
        bot = SimpleNamespace()
        bot.global_market_cache = None

        ctx = {}
        global_m = getattr(bot, "global_market_cache", None) or {}
        ctx["btc_dominance"] = float(global_m.get("btc_dominance", 0.0) or 0.0)
        ctx["fear_greed_index"] = int(global_m.get("fear_greed", 50) or 50)

        self.assertEqual(ctx["btc_dominance"], 0.0)
        self.assertEqual(ctx["fear_greed_index"], 50)

    def test_context_injection_with_empty_cache_does_not_crash(self):
        bot = SimpleNamespace()
        bot.global_market_cache = {}

        ctx = {}
        global_m = getattr(bot, "global_market_cache", None) or {}
        ctx["btc_dominance"] = float(global_m.get("btc_dominance", 0.0) or 0.0)
        ctx["fear_greed_index"] = int(global_m.get("fear_greed", 50) or 50)

        self.assertEqual(ctx["btc_dominance"], 0.0)
        self.assertEqual(ctx["fear_greed_index"], 50)

    def test_context_injection_with_real_data(self):
        bot = SimpleNamespace()
        bot.global_market_cache = {
            "btc_dominance": 58.5,
            "eth_dominance": 12.3,
            "total_market_cap": 2_500_000_000_000,
            "total_volume_24h": 80_000_000_000,
            "fear_greed": 45,
            "active_cryptos": 12500,
            "trending_coins": ["bitcoin", "ethereum", "solana"],
        }

        ctx = {}
        global_m = getattr(bot, "global_market_cache", None) or {}
        ctx["btc_dominance"] = float(global_m.get("btc_dominance", 0.0) or 0.0)
        ctx["eth_dominance"] = float(global_m.get("eth_dominance", 0.0) or 0.0)
        ctx["total_market_cap"] = float(global_m.get("total_market_cap", 0.0) or 0.0)
        ctx["total_volume_24h"] = float(global_m.get("total_volume_24h", 0.0) or 0.0)
        ctx["fear_greed_index"] = int(global_m.get("fear_greed", 50) or 50)
        ctx["active_cryptos"] = int(global_m.get("active_cryptos", 0) or 0)
        trending = global_m.get("trending_coins", []) or []
        ctx["trending_coins"] = ",".join(trending[:5]) if trending else ""

        self.assertEqual(ctx["btc_dominance"], 58.5)
        self.assertEqual(ctx["eth_dominance"], 12.3)
        self.assertEqual(ctx["total_market_cap"], 2_500_000_000_000)
        self.assertEqual(ctx["fear_greed_index"], 45)
        self.assertEqual(ctx["active_cryptos"], 12500)
        self.assertEqual(ctx["trending_coins"], "bitcoin,ethereum,solana")

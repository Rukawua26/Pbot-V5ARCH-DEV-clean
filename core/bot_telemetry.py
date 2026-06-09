from core.time_utils import monotonic_now


def collect_telemetry(bot, logger):
    """Collect runtime/service metrics for UI refresh."""
    stats = {}
    try:
        # 1. Trading stats (via Brain)
        with bot.db_lock:
            maturity = bot.brain.get_ai_maturity()
            brain_stats = bot.brain.get_stats()
            pnl_data = bot.brain.get_daily_real_pnl(bot.balance)
            shadow_wr = brain_stats.get("shadow_win_rate", 50.0)
            real_wr = brain_stats.get("real_win_rate")

            stats.update(
                {
                    "ai_xp": maturity.get("xp_percent", 0),
                    "rank": maturity.get("rank", "BRONZE"),
                    "daily_pnl": pnl_data[0] if isinstance(pnl_data, tuple) else pnl_data,
                    "total_real_trades": brain_stats.get("total_trades", 0),
                    "total_shadow_trades": brain_stats.get("shadow_trades", 0),
                    "win_rate": shadow_wr,
                    "shadow_win_rate": shadow_wr,
                    "real_win_rate": real_wr,
                }
            )

        # 2. Market and global state data
        ws_btc_ts = float(getattr(bot, "market_btc_price_ts", 0.0) or 0.0)
        ws_btc_age = monotonic_now() - ws_btc_ts if ws_btc_ts > 0 else None
        stats.update(
            {
                "btc_price": getattr(bot, "market_btc_price", 0),
                "btc_price_source": getattr(bot, "market_btc_price_source", "UNKNOWN"),
                "btc_ws_age_seconds": ws_btc_age,
                "market_regime": getattr(bot, "market_regime", "UNKNOWN"),
                "market_regime_source": getattr(bot, "market_regime_source", "UNKNOWN"),
                "market_regime_confidence": getattr(bot, "market_regime_confidence", None),
                "btc_panic": getattr(bot, "btc_panic", False),
                "fear_greed": getattr(bot, "fear_greed", 50),
                "circuit_breaker": getattr(bot, "circuit_breaker_active", False),
                "risk_multiplier": getattr(bot, "risk_multiplier", 1.0),
                "cached_pairs": len(bot.data_service.data_cache),
            }
        )

        return stats
    except Exception as error:
        logger.error(f"⚠️ Error recolectando telemetría: {error}")
        return {"rank": "ERROR", "balance": bot.balance}

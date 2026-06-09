from config import Config
from tools.notifier import send_telegram_msg


def check_ml_models_health(bot, ml_monitor_available):
    """Verifica la salud de los modelos ML."""
    is_healthy = True
    if not ml_monitor_available or not bot.ml_monitor:
        return is_healthy

    try:
        results = bot.ml_monitor.check_all_health()
        unhealthy = [
            name for name, value in results.items() if value.get("health_status") == "unhealthy"
        ]
        if unhealthy:
            message = f"⚠️ Modelos ML en mal estado: {unhealthy}"
            bot.log(message)
            send_telegram_msg(message)
            is_healthy = False

        bot.log("")
        bot.log("═" * 50)
        bot.log("🤖 SNIPER AI - MÉTRICAS ML")
        bot.log("═" * 50)

        model_names = list(results.keys())
        if model_names:
            bot.log(f"📦 Modelos activos: {', '.join(model_names)}")
            for name, result in results.items():
                health = result.get("health_status", "unknown")
                status_icon = "✅" if health == "healthy" else "❌"
                bot.log(f"   {status_icon} {name}: {health}")

                latency = result.get("latency", {})
                if latency:
                    bot.log(
                        f"      Latencia: P50={latency.get('p50', 0):.1f}ms | P95={latency.get('p95', 0):.1f}ms | P99={latency.get('p99', 0):.1f}ms"
                    )

                err = result.get("error_rate", 0)
                bot.log(f"      Error rate: {err * 100:.2f}%")
        else:
            bot.log("   ⚠️ No hay modelos registrados")

        if hasattr(bot, "ml_performance") and bot.ml_performance:
            perf_metrics = bot.ml_performance.calculate_metrics()
            if "accuracy" in perf_metrics:
                bot.log("")
                bot.log("📈 PERFORMANCE:")
                bot.log(f"   Accuracy:  {perf_metrics['accuracy'] * 100:.1f}%")
                bot.log(f"   Precision: {perf_metrics.get('precision', 0) * 100:.1f}%")
                bot.log(f"   Recall:    {perf_metrics.get('recall', 0) * 100:.1f}%")
                bot.log(f"   F1 Score:  {perf_metrics.get('f1', 0) * 100:.1f}%")
                bot.log(
                    f"   Trades:    {perf_metrics['total_trades']} (W:{perf_metrics.get('winning_trades', 0)} L:{perf_metrics.get('losing_trades', 0)})"
                )

                top_symbols = bot.ml_performance.get_top_symbols(min_predictions=3)
                if top_symbols:
                    bot.log("")
                    bot.log("🏆 TOP SÍMBOLOS:")
                    for index, symbol in enumerate(top_symbols[:5], 1):
                        bot.log(
                            f"   {index}. {symbol['symbol']}: {symbol['accuracy'] * 100:.1f}% ({symbol['count']} trades)"
                        )

        if hasattr(bot, "ml_alerts") and bot.ml_alerts:
            try:
                recent = bot.ml_alerts.get_recent_alerts(hours=24)
                if recent:
                    bot.log("")
                    bot.log(f"🔔 ALERTAS (24h): {len(recent)}")
                    for alert in recent[-3:]:
                        bot.log(f"   - {alert.get('message', 'Sin mensaje')}")
            except TypeError:
                try:
                    recent = bot.ml_alerts.get_recent_alerts()
                    if recent:
                        bot.log("")
                        bot.log(f"🔔 ALERTAS: {len(recent)}")
                        for alert in recent[-3:]:
                            bot.log(f"   - {alert.get('message', 'Sin mensaje')}")
                except Exception as error:
                    bot.log(f"⚠️ No se pudo leer alertas ML legacy: {error}")

        if Config.ML_HEALTH_VETO_ENABLED:
            perf_metrics = getattr(bot, "ml_performance", None)
            if perf_metrics:
                metrics = perf_metrics.calculate_metrics()
                if metrics.get("accuracy", 1.0) < Config.ML_HEALTH_MIN_ACCURACY:
                    bot.log(f"🛑 VETO ML: Accuracy baja ({metrics.get('accuracy', 0) * 100:.1f}%)")
                    is_healthy = False

        return is_healthy
    except Exception as error:
        bot.log(f"⚠️ Error verificando salud ML: {error}")
        return True

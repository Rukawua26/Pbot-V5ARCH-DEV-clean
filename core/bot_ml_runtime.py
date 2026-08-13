import sqlite3
import time


def init_ml_monitoring(bot, ml_monitor_available):
    """Inicializa el monitoreo de modelos ML."""
    if not ml_monitor_available or not bot.ml_monitor:
        return

    try:
        import numpy as np

        from core.strategy.consensus_nn import AgentConsensusNN
        from tools.ml_monitor import AlertManager, ModelPerformanceTracker

        neural_nn = AgentConsensusNN()
        if neural_nn.is_trained:
            baseline = np.random.randn(500, 13)
            bot.ml_monitor.register_model("neural_consensus", neural_nn, baseline)
            bot.log("✅ ML Monitor: Neural Consensus registrado")

        if bot.ghost_model is not None:
            baseline = np.random.randn(500, 20)
            bot.ml_monitor.register_model("ghost_model", bot.ghost_model, baseline)
            bot.log("✅ ML Monitor: Ghost Model registrado")

        bot.ml_performance = ModelPerformanceTracker()
        bot.ml_alerts = AlertManager()
        bot.log("✅ ML Monitor: Performance Tracker y Alert Manager inicializados")

        bot.log("✅ ML Monitor inicializado completo")

    except Exception as error:
        bot.log(f"⚠️ Error inicializando ML Monitor: {error}")


def check_recent_mfe_health(bot):
    try:
        now_ts = time.time()
        if now_ts - float(bot._mfe_alert_last_ts) < 900:
            return
        conn = sqlite3.connect(bot.brain.db_name)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT mfe_percent
            FROM trades
            WHERE is_shadow = 1
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()
        conn.close()
        if not rows:
            return
        values = [float(row["mfe_percent"] or 0.0) for row in rows]
        avg_mfe = sum(values) / max(1, len(values))
        if avg_mfe < 0.1:
            bot._mfe_alert_last_ts = now_ts
            bot.log(f"⚠️ EXIT_MFE_ALERT: MFE medio últimos 5 trades={avg_mfe:.3f}% (<0.1%).")
    except Exception as error:
        bot.log(f"⚠️ Error chequeando scorecard diario: {error}")

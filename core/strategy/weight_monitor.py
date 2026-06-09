from __future__ import annotations

import logging
from collections import deque
from typing import Any

from config import Config
from core.execution_telemetry import append_execution_event
from tools.notifier import send_telegram_msg

logger = logging.getLogger("SniperAI")


class AgentWeightMonitor:
    """
    Monitorea la degradación del rendimiento de los agentes a lo largo del tiempo.
    Detecta cuando un agente pierde efectividad significativa y alerta.

    Alinea con los IDs del orchestrator: MT, SR, G.
    """

    def __init__(self, brain_instance):
        self.brain = brain_instance
        self.history: dict[str, deque] = {
            agent: deque(maxlen=Config.AGENT_MONITOR_WINDOW) for agent in ["MT", "SR", "G"]
        }
        self.last_alerted: dict[str, float] = {}
        self.evaluation_count = 0

    def evaluate(self) -> list[dict[str, Any]]:
        """
        Evalúa el rendimiento actual de cada agente contra su historial.
        Retorna una lista de reportes de degradación (vacíos si todo ok).
        """
        if self.brain is None:
            return []

        perf = self.brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        reports: list[dict[str, Any]] = []

        for agent in ["MT", "SR", "G"]:
            current = perf.get(agent, 100.0)
            self.history[agent].append(current)

            if len(self.history[agent]) < Config.AGENT_MIN_TRADES_BEFORE_ALERT:
                continue

            prior = list(self.history[agent])[:-1]
            if not prior:
                continue
            prior_avg = sum(prior) / len(prior)

            if prior_avg > 0 and current < prior_avg * Config.AGENT_DEGRADATION_THRESHOLD:
                pct_drop = ((prior_avg - current) / prior_avg) * 100
                last = self.last_alerted.get(agent, 0.0)
                if abs(current - last) > 5.0:
                    self.last_alerted[agent] = current
                    reports.append(
                        {
                            "agent": agent,
                            "current_score": round(current, 1),
                            "prior_avg": round(prior_avg, 1),
                            "drop_pct": round(pct_drop, 1),
                            "history": [round(x, 1) for x in self.history[agent]],
                        }
                    )

        self.evaluation_count += 1
        return reports

    def alert_if_degraded(self, reports: list[dict[str, Any]]) -> None:
        """
        Envía alerta Telegram por cada reporte de degradación.
        """
        for r in reports:
            msg = (
                f"🤖 AGENT DEGRADATION: {r['agent']} "
                f"dropped {r['prior_avg']} → {r['current_score']} "
                f"({r['drop_pct']}% loss). Review recent regime changes."
            )
            logger.warning(msg)
            try:
                send_telegram_msg(msg)
            except Exception as e:
                logger.error(f"Failed to send degradation alert: {e}")

    def run_check(self, bot=None) -> list[dict[str, Any]]:
        """
        Conveniencia: ejecuta evaluación y alerta en un solo paso.
        Retorna los reportes para logging.
        """
        reports = self.evaluate()
        if reports:
            self.alert_if_degraded(reports)
            for r in reports:
                append_execution_event(
                    bot,
                    "AGENT_WEIGHT_MONITOR",
                    {
                        "agent": r["agent"],
                        "current_score": r["current_score"],
                        "prior_avg": r["prior_avg"],
                        "drop_pct": r["drop_pct"],
                    },
                )
        return reports

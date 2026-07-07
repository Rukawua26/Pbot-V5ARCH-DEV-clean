import logging
from collections import deque
from typing import Any

import numpy as np
import pandas as pd

from config import Config
from core.strategy.agents.ghost_agent import GhostAgent
from core.strategy.agents.mt_agent import MTAgent
from core.strategy.agents.sr_agent import SRAgent
from core.strategy.consensus_nn import AgentConsensusNN
from tools.learning import shadow_logger

logger = logging.getLogger("SniperAI")
CORRELATION_VETO_WINDOW = 30


class StrategyOrchestrator:
    """
    [ESTRATEGIA ORQUESTADA v118-TRINITY]
    Coordina la Trinidad de agentes:
    - MT (Tendencia)
    - SR (Estructura)
    - G (IA)
    Aplica pesos adaptativos y veto de correlación.
    """

    def __init__(self):
        self.adx_threshold = float(getattr(Config, "ADX_TREND_THRESHOLD", 20))
        self.hurst_enabled = bool(getattr(Config, "HURST_ENABLED", True))
        self.hurst_persistent_threshold = float(getattr(Config, "HURST_PERSISTENT_THRESHOLD", 0.55))
        self.hurst_antipersistent_threshold = float(
            getattr(Config, "HURST_ANTIPERSISTENT_THRESHOLD", 0.45)
        )
        self.hurst_mt_boost = float(getattr(Config, "HURST_MT_BOOST", 0.10))
        self.hurst_sr_boost = float(getattr(Config, "HURST_SR_BOOST", 0.10))
        self.agents = {
            "MT": MTAgent(),
            "SR": SRAgent(),
            "G": GhostAgent(),
        }
        self.consensus_nn = AgentConsensusNN()
        self._base_weights = self._initialize_base_weights()
        # Historial para cálculo de correlación de Pearson.
        self.vote_history = {name: deque(maxlen=CORRELATION_VETO_WINDOW) for name in self.agents}

    def _initialize_base_weights(self) -> dict[str, dict[str, float]]:
        """Pesos base para la Trinidad (MT/SR/G)."""
        return {
            "BULL_TREND": {
                "MT": 0.50,
                "SR": 0.15,
                "G": 0.35,
            },
            "BEAR_TREND": {
                "MT": 0.35,
                "SR": 0.30,
                "G": 0.35,
            },
            "RANGE": {
                "MT": 0.05,
                "SR": 0.45,
                "G": 0.50,
            },
        }

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        total = sum(weights.values())
        if total <= 0:
            return weights
        return {agent: weight / total for agent, weight in weights.items()}

    def _apply_correlation_veto(
        self,
        weights: dict[str, float],
        votes: dict[str, float],
        agent_performances: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """
        Hard-Veto de Correlación v118.1: Si la correlación entre dos agentes
        supera 0.90 en una ventana de 30 votos, el de menor WR histórico queda en peso 0.
        """
        # Actualizar historial
        for name, vote in votes.items():
            self.vote_history[name].append(vote)

        # Si no hay suficiente historial, no aplicar veto.
        if len(list(self.vote_history.values())[0]) < CORRELATION_VETO_WINDOW:
            return weights

        adjusted_weights = weights.copy()
        agent_names = list(self.agents.keys())

        # Calcular correlación cruzada
        try:
            for i in range(len(agent_names)):
                for j in range(i + 1, len(agent_names)):
                    a1, a2 = agent_names[i], agent_names[j]
                    h1, h2 = list(self.vote_history[a1]), list(self.vote_history[a2])

                    if len(h1) >= CORRELATION_VETO_WINDOW and len(h2) >= CORRELATION_VETO_WINDOW:
                        corr = np.corrcoef(h1, h2)[0, 1]
                        if not np.isnan(corr) and abs(corr) > 0.90:
                            # Excluir el agente correlacionado con menor rendimiento.
                            perf1 = (
                                agent_performances.get(a1, 100.0) if agent_performances else 100.0
                            )
                            perf2 = (
                                agent_performances.get(a2, 100.0) if agent_performances else 100.0
                            )

                            if perf1 < perf2:
                                adjusted_weights[a1] = 0.0
                            else:
                                adjusted_weights[a2] = 0.0
        except Exception as e:
            logger.error(f"Error en Veto de Correlación: {e}")

        return adjusted_weights

    def get_adaptive_weights(
        self,
        regime: str,
        agent_performances: dict[str, float] | None = None,
        adx: float | None = None,
        rsi: float | None = None,
        hurst: float | None = None,
    ) -> dict[str, float]:
        """Calcula los pesos finales basados en el régimen, rendimiento y Hurst."""
        target_regime = regime if regime in self._base_weights else "RANGE"
        adx_value = float(adx) if adx is not None else None
        rsi_value = float(rsi) if rsi is not None else None
        hurst_value = float(hurst) if hurst is not None else None

        weights = self._base_weights.get(target_regime, self._base_weights["RANGE"]).copy()

        # El HMM define el régimen; ADX/RSI solo ajustan la agresividad interna.
        if adx_value is not None:
            if target_regime in ["BULL_TREND", "BEAR_TREND"]:
                if adx_value >= self.adx_threshold:
                    weights["MT"] += 0.10
                    weights["SR"] = max(0.05, weights["SR"] - 0.05)
                else:
                    weights["MT"] = max(0.25, weights["MT"] - 0.10)
                    weights["SR"] += 0.05
            elif target_regime == "RANGE" and adx_value >= self.adx_threshold:
                weights["MT"] += 0.05
                weights["G"] = max(0.35, weights["G"] - 0.05)

        if target_regime == "RANGE" and rsi_value is not None:
            if rsi_value <= 35.0 or rsi_value >= 65.0:
                weights["SR"] += 0.10
                weights["MT"] = max(0.02, weights["MT"] - 0.03)

        # [HURST] Ajuste dinámico por memoria de mercado
        if self.hurst_enabled and hurst_value is not None:
            if hurst_value >= self.hurst_persistent_threshold:
                weights["MT"] += self.hurst_mt_boost
                weights["SR"] = max(0.05, weights["SR"] - self.hurst_sr_boost * 0.5)
            elif hurst_value <= self.hurst_antipersistent_threshold:
                weights["SR"] += self.hurst_sr_boost
                weights["MT"] = max(0.05, weights["MT"] - self.hurst_mt_boost * 0.5)

        weights = self._normalize_weights(weights)

        if not agent_performances:
            return weights

        perf_factor: dict[str, float] = {}
        for agent in weights:
            perf = agent_performances.get(agent, 100.0)
            if perf > 120:
                perf_factor[agent] = 1.3
            elif perf < 60:
                perf_factor[agent] = 0.1
            else:
                perf_factor[agent] = 1.0

        total_adjusted = sum(weights[a] * perf_factor.get(a, 1.0) for a in weights)
        if total_adjusted > 0:
            for agent in weights:
                weights[agent] = (weights[agent] * perf_factor.get(agent, 1.0)) / total_adjusted

        return weights

    def calculate_consensus(
        self,
        context: dict[str, Any],
        agent_performances: dict[str, float] | None = None,
    ) -> tuple[float, dict[str, float], dict[str, float]]:
        """Ejecuta los 3 agentes, aplica pesos y consenso neuronal.

        Returns:
            (p_final, votes, final_weights)
        """
        votes: dict[str, float] = {}

        # Ejecución de agentes
        for name, agent in self.agents.items():
            try:
                votes[name] = agent.vote(context)
            except Exception as e:
                logger.error(f"Error en agente {name}: {e}")
                votes[name] = 50.0

        # Pesos adaptativos por régimen
        regime = context.get("regime", "RANGE")
        adx = context.get("adx")
        rsi = context.get("rsi")
        hurst = context.get("hurst")
        weights = self.get_adaptive_weights(regime, agent_performances, adx, rsi, hurst)

        # Telemetría Asíncrona (Shadow Logging v118)
        shadow_logger.log(
            {
                "type": "AGENT_VOTES",
                "data": {
                    "symbol": context.get("symbol", "UNKNOWN"),
                    "votes": votes,
                    "regime": regime,
                },
            }
        )

        # Telemetria granular para auditoria de correlacion (MT/SR/G)
        ts_now = pd.Timestamp.now().isoformat()
        for agent_name, vote_value in votes.items():
            shadow_logger.log(
                {
                    "type": "AGENT_VOTE",
                    "data": {"agent": agent_name, "vote": vote_value, "ts": ts_now},
                }
            )
        # APLICAR VETO DE CORRELACIÓN (v118.1)
        final_weights = self._apply_correlation_veto(weights, votes, agent_performances)

        # Media pesada con pesos de veto
        p_final = sum(votes[a] * final_weights[a] for a in votes)
        p_final = max(0.0, min(100.0, p_final))

        # Consenso Neuronal
        nn_prob, nn_conf = self.consensus_nn.predict(votes)
        if nn_conf > 0.4:  # Aumentamos influencia si hay confianza
            p_final = (p_final * 0.6) + (nn_prob * 100 * 0.4)

        # [BREAKOUT_CHECK] Penalizar si no hay breakout ready en régimen de rango o bear
        if not context.get("breakout_ready", False):
            regime = context.get("regime", "RANGE")
            if regime in ("RANGE", "BEAR_TREND"):
                p_final *= 0.95  # 5% penalización sin breakout confirmado

        return float(p_final), votes, final_weights

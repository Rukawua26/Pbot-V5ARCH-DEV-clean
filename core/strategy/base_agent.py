import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("SniperAI")


class BaseAgent(ABC):
    """
    Clase base para todos los agentes de estrategia.
    Define la interfaz estándar que cada agente debe implementar.
    """

    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight

    @abstractmethod
    def vote(self, context: dict[str, Any]) -> float:
        """
        Calcula el voto del agente (0-100).
        0: Bajista extremo / No compra
        50: Neutral
        100: Alcista extremo / Compra fuerte
        """
        pass

    def log_vote(self, symbol: str, score: float, side: str):
        """Utilidad opcional para loguear votos específicos si es necesario."""
        if score > 70 or score < 30:
            logger.debug(f"Agent {self.name} | {symbol} | {side} | Score: {score}")

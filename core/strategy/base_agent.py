from abc import ABC, abstractmethod
from typing import Any


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

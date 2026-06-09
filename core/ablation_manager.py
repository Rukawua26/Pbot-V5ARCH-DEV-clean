import pandas as pd

from config import Config

# Asumimos que VectorBacktester está disponible en core.backtester
# from core.backtester import VectorBacktester


class AblationManager:
    """
    Fase 1: Sprint 1 - Orquestador de Matriz de Ablación.
    Compara el impacto marginal de cada módulo sobre el Baseline.
    """

    def __init__(self, data_path: str):
        self.data_path = data_path
        self.modules_to_test = [
            "EXIT_ENGINE_V1_ENABLED",
            "HMM_REGIME_ENABLED",
            "OI_FILTER_ENABLED",
            "CVD_FILTER_ENABLED",
            "MTF_FILTER_ENABLED",
            "RAG_ENABLED",
        ]

    def run_study(self):
        results = []

        # 1. Ejecutar Baseline
        print("🚀 Corriendo BASELINE...")
        baseline_res = self._run_with_config(Config.ABLATION_PROFILES["BASELINE"])
        results.append({"module": "BASELINE", **baseline_res})

        # 2. Ejecutar Baseline + 1 Módulo (Ablación Causal)
        for module in self.modules_to_test:
            print(f"🔬 Testeando impacto marginal de: {module}")
            test_config = Config.ABLATION_PROFILES["BASELINE"].copy()
            test_config[module] = True

            res = self._run_with_config(test_config)
            results.append({"module": module, **res})

        return pd.DataFrame(results)

    def _run_with_config(self, experiment_config):
        # Aquí se aplicaría la config temporalmente y se llamaría al backtester
        # Simulación de métricas para el ejemplo
        return {
            "profit_factor": 1.2,
            "win_rate": 55.0,
            "expectancy": 0.5,
            "max_dd": 15.0,
            "trade_count": 100,
        }

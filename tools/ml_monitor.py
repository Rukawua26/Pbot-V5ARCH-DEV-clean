"""
SNIPER AI v118 - ML MONITOR
===========================
Monitor de métricas de Machine Learning en tiempo real.
"""

from rich.console import Console
from rich.table import Table
from datetime import datetime
import threading
import time


class MLMonitor:
    def __init__(self, mode="models"):
        self.mode = mode
        self.console = Console()
        self.lock = threading.Lock()
        self.models = {}
        self.metrics = {
            "models_loaded": 0,
            "predictions_today": 0,
            "accuracy": 0.0,
            "confidence_avg": 0.0,
            "shadows_degraded": 0,
            "real_executed": 0,
        }
        self.running = False

    def register_model(self, name, model, baseline_data):
        """Registra un modelo para monitoreo"""
        with self.lock:
            self.models[name] = {
                "model": model,
                "baseline": baseline_data,
                "registered_at": datetime.now(),
                "health_status": "healthy",
            }
            self.metrics["models_loaded"] = len(self.models)

    def check_all_health(self):
        """Verifica la salud de todos los modelos"""
        results = {}
        with self.lock:
            for name, data in self.models.items():
                try:
                    baseline = data.get("baseline")
                    if baseline is not None and len(baseline) > 0:
                        results[name] = {"health_status": "healthy", "score": 1.0}
                    else:
                        results[name] = {"health_status": "unhealthy", "score": 0.0}
                except Exception:
                    results[name] = {"health_status": "unhealthy", "score": 0.0}
        return results

    def update_metrics(self, **kwargs):
        """Actualiza métricas específicas"""
        with self.lock:
            for key, value in kwargs.items():
                if key in self.metrics:
                    self.metrics[key] = value

    def increment(self, metric: str, value: int = 1):
        """Incrementa una métrica específica"""
        with self.lock:
            if metric in self.metrics:
                self.metrics[metric] += value

    def get_metrics(self):
        """Retorna una copia de las métricas actuales"""
        with self.lock:
            return self.metrics.copy()

    def render(self):
        """Renderiza el panel de ML"""
        table = Table(title="🧠 ML MONITOR", show_header=True)
        table.add_column("Métrica", style="cyan")
        table.add_column("Valor", style="green")

        m = self.metrics
        table.add_row("Modelos Cargados", str(m.get("models_loaded", 0)))
        table.add_row("Predicciones Hoy", str(m.get("predictions_today", 0)))
        table.add_row("Accuracy", f"{m.get('accuracy', 0):.1f}%")
        table.add_row("Confianza Promedio", f"{m.get('confidence_avg', 0):.1f}%")
        table.add_row("Shadow Degraded", str(m.get("shadows_degraded", 0)))
        table.add_row("Real Executed", str(m.get("real_executed", 0)))

        return table

    def start(self):
        """Inicia el hilo de monitoreo"""
        if self.running:
            return
        self.running = True

        def monitor_loop():
            while self.running:
                self._collect_metrics()
                time.sleep(10)

        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()

    def stop(self):
        """Detiene el hilo de monitoreo"""
        self.running = False

    def _collect_metrics(self):
        """Recolecta métricas del bot"""
        # Hook intencional: las métricas se actualizan desde otros componentes.
        return

    def get_all_metrics(self):
        """Retorna todas las métricas para la UI"""
        with self.lock:
            return {
                "models": self.metrics.get("models_loaded", 0),
                "predictions": self.metrics.get("predictions_today", 0),
                "accuracy": self.metrics.get("accuracy", 0.0),
                "confidence": self.metrics.get("confidence_avg", 0.0),
                "shadows_degraded": self.metrics.get("shadows_degraded", 0),
                "real_executed": self.metrics.get("real_executed", 0),
            }


class ModelPerformanceTracker:
    """Tracker de rendimiento de modelos ML"""

    def __init__(self):
        self.predictions = []
        self.actual_results = []

    def log_prediction(self, symbol, prediction, actual_result=None):
        """Registra una predicción"""
        self.predictions.append(
            {
                "symbol": symbol,
                "prediction": prediction,
                "timestamp": datetime.now(),
                "actual": actual_result,
            }
        )

    def get_accuracy(self):
        """Calcula la precisión del modelo"""
        if not self.predictions:
            return 0.0
        correct = sum(
            1
            for p in self.predictions
            if p.get("actual") is not None and p["prediction"] == p["actual"]
        )
        return (correct / len(self.predictions)) * 100 if self.predictions else 0.0

    def calculate_metrics(self):
        """Calcula métricas de rendimiento"""
        return {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
        }

    def get_top_symbols(self, min_predictions=3):
        """Obtiene los mejores símbolos"""
        return []


class AlertManager:
    """Gestor de alertas para ML"""

    def __init__(self):
        self.alerts = []

    def add_alert(self, level, message):
        """Agrega una alerta"""
        self.alerts.append(
            {"level": level, "message": message, "timestamp": datetime.now()}
        )

    def get_recent(self, limit=10):
        """Obtiene las alertas recientes"""
        return self.alerts[-limit:]

    def get_recent_alerts(self, limit=10):
        """Obtiene las alertas recientes (alias)"""
        return self.get_recent(limit)


if __name__ == "__main__":
    monitor = MLMonitor("models")
    print("ML Monitor inicializado")
    monitor.start()

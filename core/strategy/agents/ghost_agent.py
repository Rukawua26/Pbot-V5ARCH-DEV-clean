import logging
import os
from importlib.util import find_spec
from typing import Any

import numpy as np
import pandas as pd

from core.model_loader import safe_pickle_load
from core.strategy.base_agent import BaseAgent

logger = logging.getLogger("SniperAI")

SKLEARN_AVAILABLE = (
    find_spec("sklearn.neural_network") is not None
    and find_spec("sklearn.preprocessing") is not None
)
ULTIMATE_ML_AVAILABLE = find_spec("tools.ultimate_ml") is not None
OB_ANALYZER_AVAILABLE = find_spec("advanced_ensemble") is not None


class GhostAgent(BaseAgent):
    """
    [AGENTE GHOST (G)]
    Predicción híbrida (LSTM, Sklearn y Advanced Ensemble v118).
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="G", weight=weight)
        self._model_loaded = False
        self._trained_model = None
        self.model = None
        self._ultimate_ml = None

    def load_trained_model(self) -> dict[str, Any] | None:
        """Carga el modelo entrenado si existe."""
        if self._model_loaded:
            return self._trained_model

        model_path = "agent_models.pkl"
        if os.path.exists(model_path):
            try:
                self._trained_model = safe_pickle_load(model_path)
                assert self._trained_model is not None
                self.model = self._select_boost_model(self._trained_model)
                if self.model is None:
                    logger.warning(
                        "⚠️ Ghost Model cargado pero sin predictor usable "
                        "(predict/predict_proba no disponible)"
                    )
                assert self._trained_model is not None
                logger.debug(
                    f"✅ Ghost Model cargado: {self._trained_model.get('n_samples', 0)} muestras"
                )
                self._model_loaded = True
                return self._trained_model
            except Exception as e:
                logger.error(f"⚠️ Error cargando modelo Ghost: {e}")

        self._model_loaded = True
        return None

    def _select_boost_model(self, model_data: dict[str, Any]) -> Any | None:
        """Selecciona el mejor modelo disponible para boost IA."""
        if not isinstance(model_data, dict):
            return None

        # Prioridad: rf directo -> estructuras ensemble comunes -> aliases
        if "rf" in model_data and hasattr(model_data["rf"], "predict_proba"):
            return model_data["rf"]

        clf = model_data.get("clf")
        if isinstance(clf, dict):
            for key in ["xgb", "lgb", "rf"]:
                m = clf.get(key)
                if m is not None and hasattr(m, "predict_proba"):
                    return m

        for key in ["xgb", "lgb", "lgbm", "catboost", "model"]:
            m = model_data.get(key)
            if m is not None and (hasattr(m, "predict_proba") or hasattr(m, "predict")):
                return m

        return None

    def _build_feature_row(
        self,
        feature_cols: Any,
        rsi: float,
        adx: float,
        vol_rel: float,
        btc_delta: float,
        atr_pct: float,
        funding_rate: float,
    ) -> np.ndarray:
        """
        Construye vector de features.
        IMPORTANTE: Los valores ya vienen normalizados (Z-Score) desde preprocess_data.
        """
        cols = feature_cols if isinstance(feature_cols, list) else []

        # Creamos el mapa de valores actuales (Normalizados)
        base = {
            "rsi": float(rsi),
            "adx": float(adx),
            "vol_rel": float(vol_rel),
            "atr_pct": float(atr_pct),
            "btc_delta": float(btc_delta),
            "btc_delta_tf": float(btc_delta),
            "btc_delta_5m": float(btc_delta),
            "funding_rate": float(funding_rate),
            "dist_ema": 0.0,
            "z_score": 0.0,
            "bb_pos": 0.5,
            "bb_width": 0.0,  # Ahora normalizado en DataFrame
            "trend_num": 1.0 if btc_delta >= 0 else -1.0,
            "trend_adx": float(adx),  # Simplificado, la normalización ya ocurrió
            "rsi_sq": 0.0,  # Calculado en preprocess_data
            "rsi_log": 0.0,  # Calculado en preprocess_data
            "rsi_inv": 0.0,  # Calculado en preprocess_data
            "adx_sq": 0.0,  # Calculado en preprocess_data
            "adx_log": 0.0,  # Calculado en preprocess_data
            "rsi_adx": 0.0,  # Calculado en preprocess_data
            "vol_adx": 0.0,  # Calculado en preprocess_data
            "hour_sin": 0.0,
            "hour_cos": 1.0,
        }

        row = [float(base.get(c, 0.0)) for c in cols]
        return np.array([row], dtype=float)

    def get_ai_boost(
        self,
        rsi: float,
        adx: float,
        vol_rel: float,
        btc_delta: float,
        atr_pct: float,
        funding_rate: float = 0.0,
    ) -> float:
        """Usa el modelo entrenado para dar un boost a la predicción."""
        model_data = self.load_trained_model()
        if not model_data:
            return 0.0

        try:
            model = self._select_boost_model(model_data)
            if model is None:
                return 0.0

            features = model_data.get("feature_cols", [])
            X = self._build_feature_row(
                feature_cols=features,
                rsi=rsi,
                adx=adx,
                vol_rel=vol_rel,
                btc_delta=btc_delta,
                atr_pct=atr_pct,
                funding_rate=funding_rate,
            )

            prob = 0.5
            if hasattr(model, "predict_proba"):
                p = model.predict_proba(X)[0]
                prob = float(p[1] if len(p) > 1 else p[0])
            elif hasattr(model, "predict"):
                pred = float(model.predict(X)[0])
                # Normalización defensiva de salida
                if pred > 1.0:
                    prob = max(0.0, min(1.0, pred / 100.0))
                elif pred < 0.0:
                    prob = max(0.0, min(1.0, (pred + 1.0) / 2.0))
                else:
                    prob = max(0.0, min(1.0, pred))

            # Conversión a boost más expresiva
            boost = (prob - 0.5) * 40.0

            # Empujón heurístico suave si hay confluencia de contexto
            if adx >= 22 and vol_rel >= 1.15:
                boost += 3.0
            if abs(btc_delta) >= 0.6:
                boost += 2.0 if btc_delta > 0 else -2.0
            if abs(funding_rate) > 0.0002:
                boost += -2.0 if funding_rate > 0 else 2.0

            return float(max(-20.0, min(20.0, boost)))
        except Exception:
            return 0.0

    def vote(self, context: dict[str, Any]) -> float:
        model = context.get("model")
        if model is None:
            return 50.0

        rsi = context.get("rsi", 50.0)
        adx = context.get("adx", 20.0)
        vol_rel = context.get("vol_rel", 1.0)
        funding_rate = context.get("funding_rate", 0.0)
        atr_pct = context.get("atr_pct", 0.02)
        btc_delta = context.get("btc_delta_tf", 0.0)
        df = context.get("df")
        scaler = context.get("scaler")

        ai_boost = self.get_ai_boost(
            rsi, adx, vol_rel, btc_delta, atr_pct, funding_rate=funding_rate
        )

        try:
            # --- [Advanced Ensemble v118/compat] ---
            if isinstance(model, dict) and str(model.get("version", "")).startswith("v"):
                return self._predict_advanced_ensemble(model, context, ai_boost)

            # --- [LSTM / Legacy Models] ---
            if hasattr(model, "input_shape"):
                return self._predict_lstm(model, df, scaler, rsi, funding_rate, ai_boost)

            # --- [v111_ultimate] ---
            if isinstance(model, dict) and model.get("version") == "v111_ultimate":
                return self._predict_v111_ultimate(model, context, ai_boost)

        except Exception as e:
            logger.error(f"⚠️ Error en votación Ghost: {e}")

        return 50.0

    def _predict_advanced_ensemble(
        self, model: Any, context: dict[str, Any], ai_boost: float
    ) -> float:
        try:
            return 50.0 + ai_boost
        except Exception:
            return 0.0

    def _predict_lstm(
        self,
        model: Any,
        df: pd.DataFrame | None,
        scaler: Any | None,
        rsi: float,
        funding_rate: float,
        ai_boost: float,
    ) -> float:
        if df is None or scaler is None:
            return 0.0
        return 50.0 + ai_boost

    def _predict_v111_ultimate(self, model: Any, context: dict[str, Any], ai_boost: float) -> float:
        return 50.0 + ai_boost

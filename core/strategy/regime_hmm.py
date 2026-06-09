import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:  # pragma: no cover - exercised in environments without hmmlearn
    GaussianHMM = None


logger = logging.getLogger("SniperAI")


class DynamicHMMRegime:
    """Gaussian HMM macro-regime filter with deterministic state labeling."""

    def __init__(self, n_states: int = 3, lookback_candles: int = 336):
        self.n_states = int(n_states)
        self.lookback = int(lookback_candles)
        self.model = None
        if GaussianHMM is not None:
            self.model = GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
        self.scaler = StandardScaler()
        self.state_map: dict[float, str] = {}
        self.is_ready = False
        self.last_error: str | None = None

    def dependency_available(self) -> bool:
        return self.model is not None

    def _build_feature_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or len(df) < 20 or "close" not in df:
            return pd.DataFrame()

        df_copy = df.copy()
        close = pd.to_numeric(df_copy["close"], errors="coerce")
        df_copy["log_return"] = np.log(close / close.shift(1))
        df_copy["volatility"] = df_copy["log_return"].rolling(window=14).std()
        df_copy["dir_smooth"] = df_copy["log_return"].ewm(span=14).mean()
        return df_copy.dropna()[["log_return", "volatility", "dir_smooth"]]

    def _fit_features(self, df: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
        feature_frame = self._build_feature_frame(df)
        if feature_frame.empty:
            return np.array([]), feature_frame
        return self.scaler.fit_transform(feature_frame.values), feature_frame

    def _transform_features(self, df: pd.DataFrame) -> np.ndarray:
        feature_frame = self._build_feature_frame(df)
        if feature_frame.empty:
            return np.array([])
        return self.scaler.transform(feature_frame.values)

    def _map_hidden_states(
        self, feature_frame: pd.DataFrame, hidden_states: np.ndarray
    ) -> dict[float, str]:
        state_stats = []
        for state_id in range(self.n_states):
            mask = hidden_states == state_id
            if np.sum(mask) == 0:
                state_stats.append({"id": state_id, "return": 0.0, "vol": 0.0, "dir": 0.0})
                continue

            state_data = feature_frame.iloc[mask]
            state_stats.append(
                {
                    "id": state_id,
                    "return": float(state_data["log_return"].mean()),
                    "vol": float(state_data["volatility"].mean()),
                    "dir": float(state_data["dir_smooth"].mean()),
                }
            )

        if len(state_stats) != 3:
            ordered = sorted(state_stats, key=lambda item: item["return"])
            return {float(item["id"]): "RANGE" for item in ordered}

        max_abs_return = max(abs(item["return"]) for item in state_stats) or 1.0
        max_abs_dir = max(abs(item["dir"]) for item in state_stats) or 1.0
        max_vol = max(abs(item["vol"]) for item in state_stats) or 1.0

        def range_score(item):
            return (
                abs(item["return"]) / max_abs_return
                + abs(item["dir"]) / max_abs_dir
                - 0.15 * (abs(item["vol"]) / max_vol)
            )

        range_state = min(state_stats, key=range_score)
        directional = [item for item in state_stats if item["id"] != range_state["id"]]
        directional.sort(key=lambda item: item["return"])

        return {
            float(directional[0]["id"]): "BEAR_TREND",
            float(range_state["id"]): "RANGE",
            float(directional[-1]["id"]): "BULL_TREND",
        }

    def dynamic_retrain(self, df_history: pd.DataFrame) -> bool:
        if self.model is None:
            self.last_error = "hmmlearn no disponible"
            self.is_ready = False
            return False

        try:
            recent_data = df_history.tail(self.lookback)
            x_train, feature_frame = self._fit_features(recent_data)
            if len(x_train) < 100:
                self.last_error = "datos insuficientes para reentrenar"
                logger.warning("HMM: datos insuficientes para reentrenar")
                self.is_ready = False
                return False

            self.model.fit(x_train)
            hidden_states = self.model.predict(x_train)
            self.state_map = self._map_hidden_states(feature_frame, hidden_states)
            self.is_ready = True
            self.last_error = None
            logger.info(f"HMM reentrenado. Mapeo: {self.state_map}")
            return True
        except Exception as error:
            self.last_error = str(error)
            self.is_ready = False
            logger.error(f"Fallo grave en HMM retraining: {error}")
            return False

    def predict_regime(self, df_recent: pd.DataFrame) -> tuple[str, float]:
        if not self.is_ready or self.model is None:
            return "UNKNOWN", 0.0

        try:
            x_recent = self._transform_features(df_recent)
            if len(x_recent) == 0:
                return "UNKNOWN", 0.0

            last_obs = x_recent[-1].reshape(1, -1)
            probas = self.model.predict_proba(last_obs)[0]
            state_id = int(np.argmax(probas))
            confidence = float(probas[state_id])
            return self.state_map.get(state_id, "UNKNOWN"), confidence
        except Exception as error:
            self.last_error = str(error)
            logger.error(f"Fallo grave en HMM prediction: {error}")
            return "UNKNOWN", 0.0

    def predict_markov_snapshot(self, df_recent: pd.DataFrame) -> dict[str, object]:
        """Return a non-blocking, read-only friendly snapshot of HMM transition odds."""
        empty_snapshot: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "source": "HMM",
            "state": "UNKNOWN",
            "confidence": 0.0,
            "state_probs": {},
            "transition_probs": {},
            "breakout_prob": 50.0,
            "bullish_breakout_prob": 0.0,
            "bearish_reversal_prob": 0.0,
            "range_prob": 0.0,
            "model_version": "hmm_markov_v1",
            "is_ready": False,
        }
        if not self.is_ready or self.model is None:
            return empty_snapshot

        try:
            x_recent = self._transform_features(df_recent)
            if len(x_recent) == 0:
                return empty_snapshot

            last_obs = x_recent[-1].reshape(1, -1)
            current_probs = self.model.predict_proba(last_obs)[0]
            transition_matrix = getattr(self.model, "transmat_", None)
            if transition_matrix is None:
                return empty_snapshot

            next_probs = np.asarray(current_probs).dot(np.asarray(transition_matrix))
            state_id = int(np.argmax(current_probs))
            state = self.state_map.get(state_id, "UNKNOWN")
            confidence = float(current_probs[state_id])

            state_probs: dict[str, float] = {}
            transition_probs: dict[str, float] = {}
            for hidden_id, label in self.state_map.items():
                idx = int(hidden_id)
                state_probs[label] = state_probs.get(label, 0.0) + float(current_probs[idx])
                transition_probs[label] = transition_probs.get(label, 0.0) + float(next_probs[idx])

            bullish = max(0.0, min(100.0, transition_probs.get("BULL_TREND", 0.0) * 100.0))
            bearish = max(0.0, min(100.0, transition_probs.get("BEAR_TREND", 0.0) * 100.0))
            range_prob = max(0.0, min(100.0, transition_probs.get("RANGE", 0.0) * 100.0))

            return {
                "ts": datetime.now(UTC).isoformat(),
                "source": "HMM",
                "state": state,
                "confidence": confidence,
                "state_probs": state_probs,
                "transition_probs": transition_probs,
                "breakout_prob": max(bullish, bearish),
                "bullish_breakout_prob": bullish,
                "bearish_reversal_prob": bearish,
                "range_prob": range_prob,
                "model_version": "hmm_markov_v1",
                "is_ready": True,
            }
        except Exception as error:
            self.last_error = str(error)
            logger.error(f"Fallo grave en HMM Markov snapshot: {error}")
            return empty_snapshot

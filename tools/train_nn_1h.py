#!/usr/bin/env python3
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
CANDLES_PATH = ROOT / "data_storage" / "candles" / "BTC_USDT_1h.parquet"
HYPEROPT_PATH = ROOT / "config_hyperopt.json"
MODEL_OUT = ROOT / "v118_1H_consensus.pkl"


def load_hyperopt() -> dict:
    if not HYPEROPT_PATH.exists():
        raise FileNotFoundError(f"Missing {HYPEROPT_PATH}")
    data = json.loads(HYPEROPT_PATH.read_text(encoding="utf-8"))
    return data.get("params", {})


def alma_weights(window: int, offset: float, sigma: float) -> np.ndarray:
    m = int(offset * (window - 1))
    s = window / max(float(sigma), 1e-9)
    idx = np.arange(window)
    w = np.exp(-((idx - m) ** 2) / (2 * s * s))
    w_sum = w.sum()
    return w / w_sum if w_sum > 0 else np.ones(window) / window


def rolling_weighted(series: np.ndarray, weights: np.ndarray) -> np.ndarray:
    window = len(weights)
    out = np.full(series.shape[0], np.nan, dtype=float)
    if series.shape[0] < window:
        return out
    conv = np.convolve(series, weights[::-1], mode="valid")
    out[window - 1 :] = conv
    return out


def rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(arr.shape[0], np.nan, dtype=float)
    if arr.shape[0] < window:
        return out
    kernel = np.ones(window) / window
    conv = np.convolve(arr, kernel, mode="valid")
    out[window - 1 :] = conv
    return out


def rolling_entropy(returns: np.ndarray, bins: int, window: int = 20) -> np.ndarray:
    out = np.zeros(returns.shape[0], dtype=float)
    if returns.shape[0] < window:
        return out
    wins = np.lib.stride_tricks.sliding_window_view(returns, window_shape=window)
    mins = wins.min(axis=1)
    maxs = wins.max(axis=1)
    spans = maxs - mins
    safe_spans = np.where(spans <= 0, 1.0, spans)
    norm = (wins - mins[:, None]) / safe_spans[:, None]
    idx = np.floor(norm * bins).astype(int)
    idx = np.clip(idx, 0, bins - 1)
    one_hot = np.eye(bins, dtype=float)[idx]
    counts = one_hot.sum(axis=1)
    probs = counts / float(window)
    with np.errstate(divide="ignore", invalid="ignore"):
        entropy = -(probs * np.log2(probs))
    entropy = np.nan_to_num(entropy, nan=0.0, posinf=0.0, neginf=0.0).sum(axis=1)
    entropy = np.where(spans <= 0, 0.0, entropy)
    out[window - 1 :] = entropy
    return out


def simulate_agent_scores(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    vol = df["volume"].astype(float).to_numpy()

    alma_offset = float(params.get("alma_offset", 0.85))
    alma_sigma = float(params.get("alma_sigma", 6.0))
    z_th = float(params.get("z_score_threshold", 2.5))
    entropy_bins = int(params.get("entropy_bins", 10))

    # MT
    w9 = alma_weights(9, alma_offset, alma_sigma)
    w20 = alma_weights(20, alma_offset, alma_sigma)
    alma_short = rolling_weighted(close, w9)
    alma_long = rolling_weighted(close, w20)
    mom_now = np.divide(
        alma_short - alma_long, alma_long, out=np.zeros_like(close), where=np.abs(alma_long) > 1e-12
    )
    mom_prev = np.roll(mom_now, 1)
    mom_prev[0] = 0.0
    mt = np.full(close.shape[0], 50.0)
    mt = np.where((mom_now > 0.001) & (mom_prev > 0.0005), 70.0, mt)
    mt = np.where((mom_now < -0.003) & (mom_prev < -0.001), 30.0, mt)

    # SR
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr1 = high - low
    tr2 = np.abs(high - prev_close)
    tr3 = np.abs(low - prev_close)
    tr = np.maximum.reduce([tr1, tr2, tr3])
    atr = rolling_mean(tr, 14)
    sma20 = rolling_mean(close, 20)
    z_dynamic = np.divide(
        close - sma20, atr * 1.5, out=np.zeros_like(close), where=np.abs(atr) > 1e-12
    )
    z_dynamic = np.nan_to_num(z_dynamic, nan=0.0, posinf=0.0, neginf=0.0)

    ret = np.zeros_like(close)
    ret[1:] = np.diff(close) / np.where(np.abs(close[:-1]) > 1e-12, close[:-1], 1.0)
    entropy = rolling_entropy(ret, bins=max(2, entropy_bins), window=20)

    sr = np.full(close.shape[0], 50.0)
    sr = np.where(z_dynamic > z_th, 20.0 + (entropy * 5.0), sr)
    sr = np.where(z_dynamic < -z_th, 80.0 - (entropy * 5.0), sr)
    sr = np.clip(sr, 0.0, 100.0)

    # LB proxy (sin orderbook histórico, aproximación volumen+presión de vela)
    vol_ma20 = rolling_mean(vol, 20)
    vol_rel = np.divide(vol, vol_ma20, out=np.ones_like(vol), where=np.abs(vol_ma20) > 1e-12)
    candle_range = np.maximum(high - low, 1e-9)
    pressure = np.clip((close - low) / candle_range, 0.0, 1.0)

    lb = np.full(close.shape[0], 50.0)
    lb = np.where((vol_rel >= 1.2) & (pressure > 0.55), 65.0, lb)
    lb = np.where((vol_rel >= 1.2) & (pressure < 0.45), 35.0, lb)
    lb = np.where((vol_rel >= 2.5) & (pressure > 0.60), 80.0, lb)
    lb = np.where((vol_rel >= 2.5) & (pressure < 0.40), 20.0, lb)

    out = df.copy()
    out["mt_score"] = mt
    out["sr_score"] = sr
    out["lb_score"] = lb
    return out


def build_labels(
    df: pd.DataFrame,
    sl_pct: float,
    tp_pct: float,
    *,
    max_horizon_bars: int = 72,
) -> tuple[np.ndarray, np.ndarray]:
    close = df["close"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    features = df[["mt_score", "sr_score", "lb_score"]].to_numpy(dtype=float)

    X = []
    y = []

    sl = sl_pct / 100.0
    tp = tp_pct / 100.0

    n = len(df)
    for i in range(0, n - 2):
        entry = close[i]
        if entry <= 0:
            continue
        tp_price = entry * (1.0 + tp)
        sl_price = entry * (1.0 - sl)

        label = None
        horizon_end = min(n, i + 1 + max(1, int(max_horizon_bars)))
        for j in range(i + 1, horizon_end):
            hit_tp = high[j] >= tp_price
            hit_sl = low[j] <= sl_price
            if hit_tp or hit_sl:
                if hit_sl and hit_tp:
                    label = 0
                elif hit_tp:
                    label = 1
                else:
                    label = 0
                break

        if label is None:
            continue

        row = features[i]
        if np.isnan(row).any() or np.isinf(row).any():
            continue

        X.append(row)
        y.append(label)

    return np.array(X, dtype=float), np.array(y, dtype=int)


def chronological_split_indices(
    n_samples: int,
    *,
    val_fraction: float = 0.2,
    embargo_bars: int = 72,
) -> tuple[np.ndarray, np.ndarray]:
    if n_samples < 2:
        raise RuntimeError("Dataset insuficiente para split temporal")
    split = int(n_samples * (1.0 - val_fraction))
    split = max(1, min(split, n_samples - 1))
    train_end = max(1, split - max(0, int(embargo_bars)))
    train_idx = np.arange(0, train_end)
    val_idx = np.arange(split, n_samples)
    if len(train_idx) == 0 or len(val_idx) == 0:
        raise RuntimeError("Split temporal vacío; reduce embargo_bars o aumenta muestras")
    return train_idx, val_idx


def train_mlp(X: np.ndarray, y: np.ndarray, *, embargo_bars: int = 72) -> tuple[dict, float]:
    if len(X) < 100:
        raise RuntimeError(f"Dataset insuficiente para entrenar: {len(X)} filas")

    train_idx, val_idx = chronological_split_indices(len(X), embargo_bars=embargo_bars)
    X_train, X_val = X[train_idx], X[val_idx]
    y_train, y_val = y[train_idx], y[val_idx]
    if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
        raise RuntimeError("Split temporal sin ambas clases en train/validation")

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    model = MLPClassifier(
        hidden_layer_sizes=(16, 8),
        activation="tanh",
        solver="adam",
        alpha=0.01,
        learning_rate="adaptive",
        max_iter=600,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=30,
        random_state=42,
        verbose=False,
    )
    model.fit(X_train_s, y_train)

    preds = model.predict(X_val_s)
    acc = accuracy_score(y_val, preds)

    artifact = {
        "model": model,
        "scaler": scaler,
        "n_samples": int(len(X)),
        "feature_names": ["MT", "SR", "LB"],
        "timeframe": "1h",
        "sl_pct": float(params_global["stop_loss_pct"]),
        "tp_pct": float(params_global["take_profit_pct"]),
        "split": "chronological",
        "train_samples": int(len(train_idx)),
        "validation_samples": int(len(val_idx)),
        "embargo_bars": int(embargo_bars),
    }
    return artifact, float(acc)


if __name__ == "__main__":
    if not CANDLES_PATH.exists():
        raise FileNotFoundError(f"No existe dataset de velas: {CANDLES_PATH}")

    params_global = load_hyperopt()
    df = pd.read_parquet(CANDLES_PATH)
    needed = {"time", "open", "high", "low", "close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en candles: {sorted(missing)}")

    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    df_scores = simulate_agent_scores(df, params_global)

    sl_pct = float(params_global.get("stop_loss_pct", 2.49))
    tp_pct = float(params_global.get("take_profit_pct", 6.28))
    X, y = build_labels(df_scores, sl_pct=sl_pct, tp_pct=tp_pct)

    artifact, acc = train_mlp(X, y)

    with open(MODEL_OUT, "wb") as f:
        pickle.dump(artifact, f)

    print("✅ Re-entrenamiento 1H completado")
    print(f"✅ Modelo generado: {MODEL_OUT}")
    print(f"✅ Muestras usadas: {len(X)}")
    print(f"✅ Accuracy validación: {acc:.4f}")
    print(f"✅ Label ratio éxito: {y.mean():.4f}")

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    log_loss,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DB = ROOT / "sniper_brain.db"
DEFAULT_MODELS_DIR = ROOT / "models"


@dataclass
class DatasetBundle:
    x_ghost: pd.DataFrame
    x_consensus: np.ndarray
    y_class: np.ndarray
    y_reg: np.ndarray
    bootstrap_prob: np.ndarray
    timestamps: np.ndarray
    rows: int
    filtered_noise_rows: int
    valid_ghost: int
    valid_consensus: int


def _load_runtime_classes():
    from core.strategy.consensus_nn import AgentConsensusNN
    from tools.ultimate_ml import UltimateMLSystem

    return AgentConsensusNN, UltimateMLSystem


CURATED_VIEW = "vw_training_dataset"
VALID_EXIT_REASONS = ("HARD_SL", "DYNAMIC_SL", "TAKE_PROFIT")
GHOST_RUNTIME_FEATURES = [
    "rsi",
    "adx",
    "vol_rel",
    "atr_pct",
    "funding_rate",
    "btc_delta_tf",
]


def ensure_curated_training_view(db_path: Path, min_abs_pnl: float, max_abs_pnl: float) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"DROP VIEW IF EXISTS {CURATED_VIEW}")
        conn.execute(
            f"""
            CREATE VIEW {CURATED_VIEW} AS
            SELECT id, timestamp, symbol, side, pnl_percent, market_snapshot,
                   exit_reason, market_regime, entry_confidence
            FROM trades
            WHERE is_shadow = 1
              AND COALESCE(is_dirty, 0) = 0
              AND COALESCE(is_adopted, 0) = 0
              AND market_snapshot IS NOT NULL
              AND market_snapshot != ''
              AND pnl_percent IS NOT NULL
              AND pnl_percent != -99.0
              AND ABS(pnl_percent) >= {float(min_abs_pnl):.12g}
              AND ABS(pnl_percent) <= {float(max_abs_pnl):.12g}
              AND exit_reason IN ('HARD_SL', 'DYNAMIC_SL', 'TAKE_PROFIT')
            """
        )
        conn.commit()
    finally:
        conn.close()


def load_trade_rows(
    db_path: Path, min_abs_pnl: float, max_abs_pnl: float = 10.0
) -> tuple[list[sqlite3.Row], int]:
    ensure_curated_training_view(db_path, min_abs_pnl, max_abs_pnl)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    filtered_noise = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE market_snapshot IS NOT NULL
          AND market_snapshot != ''
          AND pnl_percent IS NOT NULL
          AND pnl_percent != -99.0
           AND (ABS(pnl_percent) < ? OR ABS(pnl_percent) > ? OR exit_reason = 'UNKNOWN')
        """,
        (min_abs_pnl, max_abs_pnl),
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT id, timestamp, symbol, side, pnl_percent, market_snapshot,
               exit_reason, market_regime, entry_confidence
        FROM vw_training_dataset
        ORDER BY timestamp ASC, id ASC
        """,
    ).fetchall()
    conn.close()
    return rows, int(filtered_noise)


def build_dataset(rows: list[sqlite3.Row], filtered_noise_rows: int) -> DatasetBundle:
    AgentConsensusNN, UltimateMLSystem = _load_runtime_classes()
    extractor = UltimateMLSystem()
    consensus = AgentConsensusNN(model_path=str(DEFAULT_MODELS_DIR / "_probe.pkl"))

    ghost_features = []
    consensus_features = []
    y_class = []
    y_reg = []
    bootstrap_prob = []
    timestamps = []
    valid_consensus = 0

    for row in rows:
        try:
            snap = json.loads(row["market_snapshot"] or "{}")
            if not isinstance(snap, dict):
                continue
            pnl = float(row["pnl_percent"])
            snap["timestamp"] = row["timestamp"]
            features = extractor.extract_features(snap)
            ghost_features.append(features)

            votes = snap.get("votos") or {}
            if isinstance(votes, dict) and votes:
                valid_consensus += 1
            consensus_features.append(
                [float(votes.get(agent, 50.0)) for agent in consensus.AGENT_NAMES]
            )
            y_class.append(1 if pnl > 0 else 0)
            y_reg.append(pnl)
            raw_bootstrap = snap.get("prob_final", snap.get("heuristic_confidence", 50.0))
            bootstrap_prob.append(max(0.0, min(1.0, float(raw_bootstrap) / 100.0)))
            ts = pd.to_datetime(row["timestamp"], utc=True, errors="coerce")
            timestamps.append(ts.to_datetime64() if not pd.isna(ts) else np.datetime64("NaT"))
        except Exception:
            continue

    x_ghost = pd.DataFrame(ghost_features)
    for column in GHOST_RUNTIME_FEATURES:
        if column not in x_ghost:
            x_ghost[column] = 0.0

    return DatasetBundle(
        x_ghost=x_ghost[GHOST_RUNTIME_FEATURES],
        x_consensus=np.array(consensus_features, dtype=float),
        y_class=np.array(y_class, dtype=int),
        y_reg=np.array(y_reg, dtype=float),
        bootstrap_prob=np.array(bootstrap_prob, dtype=float),
        timestamps=np.array(timestamps, dtype="datetime64[ns]"),
        rows=len(rows),
        filtered_noise_rows=filtered_noise_rows,
        valid_ghost=len(ghost_features),
        valid_consensus=valid_consensus,
    )


def validate_dataset(bundle: DatasetBundle, min_samples: int) -> None:
    if bundle.valid_ghost < min_samples:
        raise SystemExit(
            f"Dataset insuficiente: {bundle.valid_ghost} muestras válidas, mínimo {min_samples}."
        )
    class_counts = np.bincount(bundle.y_class, minlength=2)
    if int(class_counts.min()) < 5:
        raise SystemExit(f"Clases insuficientes para CV: counts={class_counts.tolist()}.")
    if bundle.x_ghost.empty or bundle.x_ghost.isnull().any().any():
        raise SystemExit("Features Ghost inválidas: NaN o dataset vacío.")
    if not np.isfinite(bundle.x_consensus).all():
        raise SystemExit("Features Consensus inválidas: valores no finitos.")
    if len(bundle.timestamps) != len(bundle.y_class) or np.isnat(bundle.timestamps).any():
        raise SystemExit("Timestamps inválidos: walk-forward requiere fechas válidas.")


def _write_sha256_sidecar(path: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(digest, encoding="utf-8")
    return sidecar


def _classification_metrics(y_true: np.ndarray, proba: np.ndarray) -> dict:
    preds = (proba >= 0.5).astype(int)
    return {
        "f1": float(f1_score(y_true, preds, zero_division=0)),
        "average_precision": float(average_precision_score(y_true, proba)),
        "brier": float(brier_score_loss(y_true, proba)),
        "log_loss": float(log_loss(y_true, proba, labels=[0, 1])),
        "predicted_wins": int(preds.sum()),
    }


def train_ghost(
    bundle: DatasetBundle,
    output_path: Path,
    positive_class_weight: float,
    holdout_ratio: float = 0.30,
    min_oos_samples: int = 60,
    min_oos_class_samples: int = 15,
) -> dict:
    _, UltimateMLSystem = _load_runtime_classes()
    split = int(len(bundle.x_ghost) * (1.0 - holdout_ratio))
    if split <= 0 or split >= len(bundle.x_ghost):
        raise SystemExit("Ghost: holdout cronologico invalido.")
    train_x = bundle.x_ghost.iloc[:split]
    train_y = bundle.y_class[:split]
    train_reg = bundle.y_reg[:split]
    oos_x = bundle.x_ghost.iloc[split:]
    oos_y = bundle.y_class[split:]
    oos_bootstrap = bundle.bootstrap_prob[split:]
    class_counts = np.bincount(oos_y, minlength=2)
    if len(oos_y) < min_oos_samples or int(class_counts.min()) < min_oos_class_samples:
        raise SystemExit(
            "Ghost: holdout OOS insuficiente para decision ciega: "
            f"samples={len(oos_y)}, classes={class_counts.tolist()}. No se publica artefacto."
        )

    trainer = UltimateMLSystem(model_path=str(output_path))
    trainer.train(
        train_x,
        train_y,
        train_reg,
        positive_class_weight=positive_class_weight,
    )

    _write_sha256_sidecar(output_path)
    with output_path.open("rb") as model_file:
        model_data = pickle.load(model_file)
    classifiers = list(model_data.get("clf", {}).values())
    regressors = list(model_data.get("reg", {}).values())
    if not classifiers or not regressors:
        raise SystemExit("Ghost: artefacto sin clasificadores/regresores utilizables.")
    oos_proba = np.mean([clf.predict_proba(oos_x)[:, 1] for clf in classifiers], axis=0)
    model_metrics = _classification_metrics(oos_y, oos_proba)
    bootstrap_metrics = _classification_metrics(oos_y, oos_bootstrap)
    prevalence = float(oos_y.mean())
    gates = {
        "average_precision_above_prevalence": model_metrics["average_precision"] > prevalence,
        "average_precision_beats_bootstrap": model_metrics["average_precision"]
        > bootstrap_metrics["average_precision"],
        "brier_improves_bootstrap_10pct": model_metrics["brier"]
        <= bootstrap_metrics["brier"] * 0.90,
        "f1_at_least_0_30": model_metrics["f1"] >= 0.30,
    }
    metrics = {
        "samples": int(len(bundle.x_ghost)),
        "train_samples": int(split),
        "oos_samples": int(len(oos_y)),
        "oos_class_counts": {"losses": int(class_counts[0]), "wins": int(class_counts[1])},
        "oos_prevalence": prevalence,
        "oos_model": model_metrics,
        "oos_bootstrap": bootstrap_metrics,
        "publication_gates": gates,
        "publication_approved": bool(all(gates.values())),
    }
    clf = model_data.get("clf", {}).get("rf")
    reg = model_data.get("reg", {}).get("rf")
    if clf is not None and hasattr(clf, "predict"):
        pred = clf.predict(train_x)
        metrics["rf_train_accuracy"] = float(accuracy_score(train_y, pred))
        metrics["rf_train_f1"] = float(f1_score(train_y, pred, zero_division=0))
    if reg is not None and hasattr(reg, "predict"):
        pred_reg = reg.predict(train_x)
        metrics["rf_train_r2"] = float(r2_score(train_reg, pred_reg))
        metrics["rf_train_rmse"] = float(np.sqrt(mean_squared_error(train_reg, pred_reg)))
    if not metrics["publication_approved"]:
        output_path.unlink(missing_ok=True)
        output_path.with_suffix(output_path.suffix + ".sha256").unlink(missing_ok=True)
    return metrics


def _oversample_positive_class(
    X: np.ndarray, y: np.ndarray, positive_class_weight: float, seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    pos_idx = np.flatnonzero(y == 1)
    neg_idx = np.flatnonzero(y == 0)
    if len(pos_idx) == 0 or len(neg_idx) == 0:
        return X, y

    target_pos = int(np.ceil(len(neg_idx) * max(1.0, positive_class_weight)))
    extra = max(0, target_pos - len(pos_idx))
    sampled_extra = (
        rng.choice(pos_idx, size=extra, replace=True) if extra else np.array([], dtype=int)
    )
    final_idx = np.concatenate([np.arange(len(y)), sampled_extra])
    rng.shuffle(final_idx)
    return X[final_idx], y[final_idx]


def build_walk_forward_windows(
    timestamps: np.ndarray,
    train_months: int = 3,
    val_months: int = 1,
    val_stride_months: int | None = None,
) -> list[dict]:
    """Construye ventanas rolling por mes: train N meses, valida M meses."""
    if train_months <= 0 or val_months <= 0:
        raise ValueError("train_months y val_months deben ser positivos")

    ts = pd.Series(pd.to_datetime(timestamps, utc=True, errors="coerce"))
    if ts.isna().any():
        return []

    periods = ts.dt.tz_convert(None).dt.to_period("M")
    months = sorted(periods.unique())
    windows = []
    last_start = len(months) - train_months - val_months + 1
    stride = max(1, int(val_stride_months or val_months))
    for start in range(0, max(0, last_start), stride):
        train_periods = months[start : start + train_months]
        val_periods = months[start + train_months : start + train_months + val_months]
        train_idx = np.flatnonzero(periods.isin(train_periods).to_numpy())
        val_idx = np.flatnonzero(periods.isin(val_periods).to_numpy())
        if len(train_idx) == 0 or len(val_idx) == 0:
            continue
        windows.append(
            {
                "name": f"{train_periods[0]}..{train_periods[-1]}_to_{val_periods[0]}..{val_periods[-1]}",
                "train_idx": train_idx,
                "val_idx": val_idx,
                "train_months": [str(p) for p in train_periods],
                "val_months": [str(p) for p in val_periods],
                "type": "rolling_month",
            }
        )
    return windows


def _chronological_holdout_window(timestamps: np.ndarray, train_ratio: float = 0.8) -> list[dict]:
    ts = pd.Series(pd.to_datetime(timestamps, utc=True, errors="coerce"))
    if ts.isna().any() or len(ts) < 2:
        return []
    ordered = np.argsort(ts.to_numpy())
    split = int(len(ordered) * train_ratio)
    split = max(1, min(split, len(ordered) - 1))
    return [
        {
            "name": "chronological_holdout_insufficient_months",
            "train_idx": ordered[:split],
            "val_idx": ordered[split:],
            "train_months": [],
            "val_months": [],
            "type": "chronological_holdout",
        }
    ]


def _fit_consensus_classifier(X_train, y_train, positive_class_weight: float):
    X_train_balanced, y_train_balanced = _oversample_positive_class(
        X_train,
        y_train,
        positive_class_weight=positive_class_weight,
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train_balanced)
    clf = MLPClassifier(
        hidden_layer_sizes=(24, 12),
        activation="tanh",
        solver="adam",
        alpha=0.005,
        learning_rate="adaptive",
        max_iter=900,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=40,
        random_state=42,
        verbose=False,
    )
    clf.fit(X_train_s, y_train_balanced)
    return clf, scaler, len(X_train_balanced)


def _select_consensus_threshold(proba, y_val, min_recall: float) -> tuple[dict, list[dict]]:
    actual_wins = int(y_val.sum())
    max_predicted_wins = min(
        len(y_val) - 1,
        max(actual_wins + 3, int(np.ceil(actual_wins * 1.5))),
    )
    threshold_rows = []
    best = None
    for threshold in np.round(np.arange(0.50, 0.901, 0.01), 2):
        preds_at_threshold = (proba >= threshold).astype(int)
        predicted_wins = int(preds_at_threshold.sum())
        precision = float(precision_score(y_val, preds_at_threshold, zero_division=0))
        recall = float(recall_score(y_val, preds_at_threshold, zero_division=0))
        f1 = float(f1_score(y_val, preds_at_threshold, zero_division=0))
        row = {
            "threshold": float(threshold),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "predicted_wins": predicted_wins,
            "actual_wins": actual_wins,
            "max_predicted_wins": int(max_predicted_wins),
        }
        threshold_rows.append(row)
        if recall < min_recall:
            continue
        if predicted_wins <= 0 or predicted_wins > max_predicted_wins:
            continue
        if best is None or (f1, precision, -predicted_wins) > (
            best["f1"],
            best["precision"],
            -best["predicted_wins"],
        ):
            best = row
    return best, threshold_rows


def train_consensus(
    bundle: DatasetBundle,
    output_path: Path,
    positive_class_weight: float,
    min_val_f1: float,
    min_recall: float,
    train_months: int = 3,
    val_months: int = 1,
    min_walk_forward_windows: int = 1,
) -> dict:
    AgentConsensusNN, _ = _load_runtime_classes()
    windows = build_walk_forward_windows(bundle.timestamps, train_months, val_months)
    if len(windows) < min_walk_forward_windows:
        windows = _chronological_holdout_window(bundle.timestamps)
    if len(windows) < min_walk_forward_windows:
        raise SystemExit("No hay ventanas temporales suficientes para validar consensus.")

    window_metrics = []
    thresholds = []
    for idx, window in enumerate(windows, start=1):
        train_idx = window["train_idx"]
        val_idx = window["val_idx"]
        X_train = bundle.x_consensus[train_idx]
        y_train = bundle.y_class[train_idx]
        X_val = bundle.x_consensus[val_idx]
        y_val = bundle.y_class[val_idx]

        if len(np.unique(y_train)) < 2:
            raise SystemExit(f"Walk-forward ventana {window['name']} sin ambas clases en train.")
        if len(np.unique(y_val)) < 2:
            raise SystemExit(
                f"Walk-forward ventana {window['name']} sin ambas clases en validación."
            )

        clf, scaler, balanced_n = _fit_consensus_classifier(
            X_train,
            y_train,
            positive_class_weight=positive_class_weight,
        )
        proba = clf.predict_proba(scaler.transform(X_val))[:, 1]
        best, threshold_rows = _select_consensus_threshold(proba, y_val, min_recall)
        if best is None:
            fallback = max(threshold_rows, key=lambda item: (item["f1"], item["precision"]))
            raise SystemExit(
                f"Consensus no robusto en ventana {window['name']}: "
                f"best_any_threshold={fallback}. No se publica artefacto."
            )

        preds = (proba >= best["threshold"]).astype(int)
        val_f1 = float(f1_score(y_val, preds, zero_division=0))
        val_recall = float(recall_score(y_val, preds, zero_division=0))
        val_precision = float(precision_score(y_val, preds, zero_division=0))
        metric = {
            "window": idx,
            "name": window["name"],
            "type": window["type"],
            "train_samples": int(len(train_idx)),
            "balanced_train_samples": int(balanced_n),
            "val_samples": int(len(val_idx)),
            "threshold": float(best["threshold"]),
            "precision": val_precision,
            "recall": val_recall,
            "f1": val_f1,
            "val_predicted_wins": int(preds.sum()),
            "val_actual_wins": int(y_val.sum()),
            "train_months": window.get("train_months", []),
            "val_months": window.get("val_months", []),
        }
        if val_f1 < min_val_f1 or val_recall < min_recall:
            raise SystemExit(
                f"Consensus no robusto en ventana {window['name']}: "
                f"f1={val_f1:.4f}, recall={val_recall:.4f}; no se publica artefacto."
            )
        window_metrics.append(metric)
        thresholds.append(float(best["threshold"]))

    final_threshold = float(np.median(thresholds))
    clf, scaler, balanced_train_samples = _fit_consensus_classifier(
        bundle.x_consensus,
        bundle.y_class,
        positive_class_weight=positive_class_weight,
    )

    output_path.write_bytes(
        pickle.dumps(
            {
                "model": clf,
                "scaler": scaler,
                "n_samples": int(balanced_train_samples),
                "source_train_samples": int(len(bundle.x_consensus)),
                "positive_class_weight": float(positive_class_weight),
                "probability_threshold": final_threshold,
                "threshold_policy": {
                    "range": "0.50..0.90 step 0.01",
                    "min_recall": float(min_recall),
                    "validation": "walk_forward_monthly",
                    "train_months": int(train_months),
                    "val_months": int(val_months),
                    "windows": window_metrics,
                },
                "agent_names": AgentConsensusNN.AGENT_NAMES,
            }
        )
    )
    _write_sha256_sidecar(output_path)
    return {
        "samples": int(len(bundle.x_consensus)),
        "train_samples": int(len(bundle.x_consensus)),
        "balanced_train_samples": int(balanced_train_samples),
        "val_samples": int(sum(row["val_samples"] for row in window_metrics)),
        "valid_vote_rows": int(bundle.valid_consensus),
        "positive_class_weight": float(positive_class_weight),
        "probability_threshold": final_threshold,
        "walk_forward_windows": window_metrics,
        "val_f1_min": float(min(row["f1"] for row in window_metrics)),
        "val_f1_mean": float(np.mean([row["f1"] for row in window_metrics])),
        "val_recall_min": float(min(row["recall"] for row in window_metrics)),
        "val_precision_mean": float(np.mean([row["precision"] for row in window_metrics])),
    }


def publish_legacy(models_dir: Path, root_dir: Path) -> None:
    copies = {
        models_dir / "agent_models.pkl": root_dir / "agent_models.pkl",
        models_dir / "agent_models.pkl.sha256": root_dir / "agent_models.pkl.sha256",
        models_dir / "v118_1H_consensus.pkl": root_dir / "v118_1H_consensus.pkl",
        models_dir / "v118_1H_consensus.pkl.sha256": root_dir / "v118_1H_consensus.pkl.sha256",
    }
    for src, dst in copies.items():
        if src.exists():
            shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline reproducible de modelos Sniper AI")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--min-abs-pnl", type=float, default=0.10)
    parser.add_argument("--max-abs-pnl", type=float, default=10.0)
    parser.add_argument("--positive-class-weight", type=float, default=3.0)
    parser.add_argument("--min-consensus-f1", type=float, default=0.35)
    parser.add_argument("--min-consensus-recall", type=float, default=0.30)
    parser.add_argument("--walk-forward-train-months", type=int, default=3)
    parser.add_argument("--walk-forward-val-months", type=int, default=1)
    parser.add_argument("--min-walk-forward-windows", type=int, default=1)
    parser.add_argument("--ghost-holdout-ratio", type=float, default=0.30)
    parser.add_argument("--ghost-min-oos-samples", type=int, default=60)
    parser.add_argument("--ghost-min-oos-class-samples", type=int, default=15)
    parser.add_argument("--no-legacy-copy", action="store_true")
    parser.add_argument("--ghost-only", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB no encontrada: {args.db}")
    args.models_dir.mkdir(parents=True, exist_ok=True)

    rows, filtered_noise_rows = load_trade_rows(args.db, args.min_abs_pnl, args.max_abs_pnl)
    bundle = build_dataset(rows, filtered_noise_rows)
    validate_dataset(bundle, args.min_samples)

    ghost_path = args.models_dir / "agent_models.pkl"
    consensus_path = args.models_dir / "v118_1H_consensus.pkl"
    ghost_tmp_path = args.models_dir / "agent_models.pkl.tmp"
    consensus_tmp_path = args.models_dir / "v118_1H_consensus.pkl.tmp"
    for tmp_path in (ghost_tmp_path, consensus_tmp_path):
        if tmp_path.exists():
            tmp_path.unlink()

    ghost_metrics = train_ghost(
        bundle,
        ghost_tmp_path,
        args.positive_class_weight,
        holdout_ratio=args.ghost_holdout_ratio,
        min_oos_samples=args.ghost_min_oos_samples,
        min_oos_class_samples=args.ghost_min_oos_class_samples,
    )
    consensus_metrics = None
    if ghost_metrics.get("publication_approved") and not args.ghost_only:
        consensus_metrics = train_consensus(
            bundle,
            consensus_tmp_path,
            positive_class_weight=args.positive_class_weight,
            min_val_f1=args.min_consensus_f1,
            min_recall=args.min_consensus_recall,
            train_months=args.walk_forward_train_months,
            val_months=args.walk_forward_val_months,
            min_walk_forward_windows=args.min_walk_forward_windows,
        )

    if ghost_metrics.get("publication_approved"):
        ghost_tmp_path.replace(ghost_path)
        ghost_tmp_path.with_suffix(ghost_tmp_path.suffix + ".sha256").replace(
            ghost_path.with_suffix(ghost_path.suffix + ".sha256")
        )
    if ghost_metrics.get("publication_approved") and not args.ghost_only:
        consensus_tmp_path.replace(consensus_path)
        consensus_tmp_path.with_suffix(consensus_tmp_path.suffix + ".sha256").replace(
            consensus_path.with_suffix(consensus_path.suffix + ".sha256")
        )

    manifest = {
        "db": str(args.db),
        "rows_loaded": bundle.rows,
        "filtered_noise_rows": bundle.filtered_noise_rows,
        "min_abs_pnl": float(args.min_abs_pnl),
        "max_abs_pnl": float(args.max_abs_pnl),
        "curated_view": CURATED_VIEW,
        "valid_ghost_rows": bundle.valid_ghost,
        "valid_consensus_vote_rows": bundle.valid_consensus,
        "class_balance": {
            "losses": int((bundle.y_class == 0).sum()),
            "wins": int((bundle.y_class == 1).sum()),
        },
        "artifacts": {
            "ghost": str(ghost_path),
            "consensus": str(consensus_path),
        },
        "metrics": {
            "ghost": ghost_metrics,
            "consensus": consensus_metrics,
        },
    }
    (args.models_dir / "training_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    if not ghost_metrics.get("publication_approved"):
        print(json.dumps(manifest, indent=2, sort_keys=True))
        raise SystemExit("Ghost no supera gates OOS; artefacto descartado.")

    if not args.no_legacy_copy:
        publish_legacy(args.models_dir, ROOT)

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

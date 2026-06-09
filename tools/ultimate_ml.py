"""
SNIPER AI v118.0 - ULTIMATE ML SYSTEM
====================================
- Regresión para predecir PnL
- Clasificación para predecir dirección
- Meta-learning: combina ambos
"""

import numpy as np
import pandas as pd
import pickle
import os
import warnings
from core.model_loader import safe_pickle_load

warnings.filterwarnings("ignore")

from xgboost import XGBClassifier, XGBRegressor
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.ensemble import (
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.model_selection import (
    cross_val_score,
    cross_val_predict,
    StratifiedKFold,
    KFold,
)
from sklearn.metrics import mean_squared_error, r2_score


class UltimateMLSystem:
    def __init__(self, model_path="agent_models.pkl"):
        self.model_path = model_path
        self.clf_models = {}
        self.reg_models = {}
        self.feature_cols = []
        self.is_trained = False

    def extract_features(self, ctx):
        """Extrae features para predicción."""
        f = {}
        f["rsi"] = ctx.get("rsi", 50)
        f["adx"] = ctx.get("adx", 20)
        f["vol_rel"] = ctx.get("vol_rel", 1.0)
        f["atr_pct"] = ctx.get("atr_pct", 0.02)
        f["z_score"] = ctx.get("z_score", 0)
        f["dist_ema"] = ctx.get("dist_ema", 0)
        f["bb_pos"] = ctx.get("bb_pos", 0.5)
        f["funding_rate"] = ctx.get("funding_rate", 0)
        f["btc_delta_tf"] = ctx.get("btc_delta_tf", 0)

        rsi, adx = f["rsi"], f["adx"]

        f["rsi_sq"] = rsi**2
        f["rsi_log"] = np.log1p(abs(rsi - 50))
        f["rsi_inv"] = 100 - rsi
        f["adx_sq"] = adx**2
        f["adx_log"] = np.log1p(adx)
        f["rsi_adx"] = (rsi - 50) * adx
        f["vol_adx"] = f["vol_rel"] * adx
        f["bb_width"] = abs(f["bb_pos"] - 0.5) * 2

        trend = ctx.get("trend", "DOWN")
        f["trend_num"] = 1 if trend == "UP" else -1
        f["trend_adx"] = f["trend_num"] * adx

        ts = pd.Timestamp.now()
        f["hour_sin"] = np.sin(2 * np.pi * ts.hour / 24)
        f["hour_cos"] = np.cos(2 * np.pi * ts.hour / 24)
        f["hour_high"] = 1 if 17 <= ts.hour <= 20 else 0
        f["dow"] = ts.dayofweek

        f["rsi_oversold"] = 1 if rsi < 30 else 0
        f["rsi_overbought"] = 1 if rsi > 70 else 0
        f["adx_strong"] = 1 if adx > 25 else 0
        f["funding_pos"] = 1 if f["funding_rate"] > 0 else 0
        f["btc_pos"] = 1 if f["btc_delta_tf"] > 0 else 0

        regime = ctx.get("regime", "CALM")
        f["regime_trend"] = 1 if regime == "TREND" else 0
        f["regime_chaos"] = 1 if regime == "CHAOS" else 0

        return f

    def train(self, X, y_class, y_regress, positive_class_weight=3.0):
        """Entrena sistema dual: clasificación + regresión.
        Recibe X ya normalizado (Z-Score dinámico).
        """
        print("🚀 ENTRENANDO ULTIMATE ML SYSTEM v118.0")
        print("=" * 50)

        self.feature_cols = list(X.columns)

        print(f"📊 Dataset: {len(X)} samples")
        print(f"   Win Rate: {y_class.mean() * 100:.1f}%")
        print(f"   Avg PnL: {y_regress.mean():.2f}%")
        pos_weight = max(float(positive_class_weight), 1.0)

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

        print("\n📈 CLASIFICADORES (Dirección):")

        xgb_c = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            random_state=42,
            use_label_encoder=False,
            eval_metric="logloss",
            scale_pos_weight=pos_weight,
        )
        scores = cross_val_score(xgb_c, X, y_class, cv=cv, scoring="f1")
        print(f"   XGBoost F1: {scores.mean():.3f}")
        xgb_c.fit(X, y_class)
        self.clf_models["xgb"] = xgb_c

        lgb_c = LGBMClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            random_state=42,
            verbose=-1,
            class_weight={0: 1.0, 1: pos_weight},
        )
        scores = cross_val_score(lgb_c, X, y_class, cv=cv, scoring="f1")
        print(f"   LightGBM F1: {scores.mean():.3f}")
        lgb_c.fit(X, y_class)
        self.clf_models["lgb"] = lgb_c

        rf_c = RandomForestClassifier(
            n_estimators=200,
            max_depth=10,
            class_weight={0: 1.0, 1: pos_weight},
            random_state=42,
            n_jobs=-1,
        )
        scores = cross_val_score(rf_c, X, y_class, cv=cv, scoring="f1")
        print(f"   RandomForest F1: {scores.mean():.3f}")
        rf_c.fit(X, y_class)
        self.clf_models["rf"] = rf_c

        print("\n📉 REGRESORES (PnL esperado):")

        cv_reg = KFold(n_splits=5, shuffle=True, random_state=42)

        xgb_r = XGBRegressor(
            n_estimators=200, max_depth=6, learning_rate=0.05, random_state=42
        )
        preds = cross_val_predict(xgb_r, X, y_regress, cv=cv_reg)
        r2 = r2_score(y_regress, preds)
        rmse = np.sqrt(mean_squared_error(y_regress, preds))
        print(f"   XGBoost R2: {r2:.3f}, RMSE: {rmse:.2f}")
        xgb_r.fit(X, y_regress)
        self.reg_models["xgb"] = xgb_r

        lgb_r = LGBMRegressor(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            random_state=42,
            verbose=-1,
        )
        preds = cross_val_predict(lgb_r, X, y_regress, cv=cv_reg)
        r2 = r2_score(y_regress, preds)
        rmse = np.sqrt(mean_squared_error(y_regress, preds))
        print(f"   LightGBM R2: {r2:.3f}, RMSE: {rmse:.2f}")
        lgb_r.fit(X, y_regress)
        self.reg_models["lgb"] = lgb_r

        rf_r = RandomForestRegressor(
            n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
        )
        preds = cross_val_predict(rf_r, X, y_regress, cv=cv_reg)
        r2 = r2_score(y_regress, preds)
        rmse = np.sqrt(mean_squared_error(y_regress, preds))
        print(f"   RandomForest R2: {r2:.3f}, RMSE: {rmse:.2f}")
        rf_r.fit(X, y_regress)
        self.reg_models["rf"] = rf_r

        self.models = {
            "clf": self.clf_models,
            "reg": self.reg_models,
            "feature_cols": self.feature_cols,
            "version": "v111_ultimate_zscore",
        }

        self.save()
        self.is_trained = True

        print("\n✅ ULTIMATE ML TRAINED (Dynamic Z-Score)")
        return self.models

    def predict(self, features_dict):
        """Predice con sistema dual.
        Asume que features_dict ya contiene los datos normalizados (Z-Score dinámico).
        """
        if not self.models:
            return 0.5, 0.0, 0.0

        feat_vec = []
        for col in self.feature_cols:
            feat_vec.append(features_dict.get(col, 0))

        X = np.array([feat_vec])

        clf_probs = []
        for name, model in self.clf_models.items():
            clf_probs.append(model.predict_proba(X)[0][1])

        reg_preds = []
        for name, model in self.reg_models.items():
            reg_preds.append(model.predict(X)[0])

        clf_avg = np.mean(clf_probs)
        reg_avg = np.mean(reg_preds)

        conf = 1 - (max(clf_probs) - min(clf_probs)) if len(clf_probs) > 1 else 0.5
        conf = max(0.3, min(1.0, conf))

        return clf_avg, reg_avg, conf

    def get_trade_decision(self, features_dict):
        """Meta-decisión: combina clasificación y regresión."""
        clf_prob, expected_pnl, confidence = self.predict(features_dict)

        if clf_prob > 0.65 and expected_pnl > 0.5:
            decision = "STRONG_BUY"
            score = (clf_prob * 0.6 + min(expected_pnl / 10, 0.4) * 0.4) * 100
        elif clf_prob > 0.55:
            decision = "BUY"
            score = clf_prob * 100
        elif clf_prob < 0.35 and expected_pnl < -0.5:
            decision = "STRONG_SELL"
            score = (
                (1 - clf_prob) * 0.6 + min(abs(expected_pnl) / 10, 0.4) * 0.4
            ) * 100
        elif clf_prob < 0.45:
            decision = "SELL"
            score = (1 - clf_prob) * 100
        else:
            decision = "HOLD"
            score = 50 + (clf_prob - 0.5) * 20

        return decision, score, expected_pnl, confidence

    def save(self):
        with open(self.model_path, "wb") as f:
            pickle.dump(self.models, f)
        print(f"💾 Saved to {self.model_path}")

    def load(self):
        if os.path.exists(self.model_path):
            try:
                self.models = safe_pickle_load(self.model_path)
                self.clf_models = self.models.get("clf", {})
                self.reg_models = self.models.get("reg", {})
                self.feature_cols = self.models.get("feature_cols", [])
                self.is_trained = True
                return True
            except Exception as e:
                print(f"Error loading: {e}")
                return False
        return False


if __name__ == "__main__":
    print("✅ Ultimate ML System ready")

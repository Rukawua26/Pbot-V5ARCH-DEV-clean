import sqlite3
import pandas as pd
import numpy as np
from tools.ultimate_ml import UltimateMLSystem


def train_ghost_brain():
    """
    Entrenador independiente del Agente Ghost.
    Extrae datos de la DB, entrena el sistema Ultimate ML y guarda el resultado.
    """
    db_path = "sniper_brain.db"
    model_path = "ghost_brain.pkl"

    print("🧠 [GHOST TRAINER] Iniciando proceso de re-entrenamiento...")

    try:
        # 1. Carga de Datos
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Solo entrenamos con trades que tengan snapshot de mercado
        c.execute(
            "SELECT * FROM trades WHERE market_snapshot IS NOT NULL AND pnl_percent IS NOT NULL"
        )
        rows = c.fetchall()
        conn.close()

        if len(rows) < 100:
            print(
                f"⚠️ Datos insuficientes para entrenamiento ({len(rows)} trades). Se requieren min 100."
            )
            return False

        # 2. Preparación de Features y Targets
        data = []
        y_class = []
        y_reg = []

        for r in rows:
            try:
                import json

                snap = json.loads(r["market_snapshot"])

                # Extraemos el contexto exacto que espera UltimateMLSystem.extract_features
                ctx = {
                    "rsi": snap.get("rsi", 50),
                    "adx": snap.get("adx", 20),
                    "vol_rel": snap.get("vol_rel", 1.0),
                    "atr_pct": snap.get("atr_pct", 0.02),
                    "z_score": snap.get("z_score", 0),
                    "dist_ema": snap.get("dist_ema", 0),
                    "bb_pos": snap.get("bb_pos", 0.5),
                    "funding_rate": snap.get("funding_rate", 0),
                    "btc_delta_tf": snap.get("btc_delta_tf", 0),
                    "trend": snap.get("trend", "DOWN"),
                    "regime": snap.get("regime", "CALM"),
                }

                # Usamos el extractor de features del sistema
                ml_system = UltimateMLSystem()
                features = ml_system.extract_features(ctx)

                data.append(features)
                y_class.append(1 if r["pnl_percent"] > 0 else 0)
                y_reg.append(r["pnl_percent"])
            except Exception:
                continue

        X = pd.DataFrame(data)
        y_class = np.array(y_class)
        y_reg = np.array(y_reg)

        # 3. Entrenamiento con UltimateMLSystem
        trainer = UltimateMLSystem(model_path=model_path)
        trainer.train(X, y_class, y_reg)

        print(f"✅ Entrenamiento completado. Modelo guardado en {model_path}")
        return True

    except Exception as e:
        print(f"❌ Error crítico durante el entrenamiento: {e}")
        return False


if __name__ == "__main__":
    success = train_ghost_brain()
    if not success:
        exit(1)
    exit(0)

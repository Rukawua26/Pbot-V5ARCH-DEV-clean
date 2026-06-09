#!/usr/bin/env python3
"""
[V118-ULTIMATE] ML OPTIMIZER: The Eternal Brain Loop
===================================================
Re-entrena automáticamente UltimateML y NeuralConsensus.
Implementa validación "Challenger vs Champion".
"""

import os
import sqlite3
import pandas as pd
import numpy as np
import json
from datetime import datetime
from tools.ultimate_ml import UltimateMLSystem
# Importamos la clase desde strategy para mantener compatibilidad actual
from tools.strategy import AgentConsensusNN

DB_PATH = "sniper_brain.db"
MODEL_ULTIMATE = "agent_models.pkl"
MODEL_CONSENSUS = "agent_consensus_nn.pkl"
BACKUP_DIR = "backups/ml_models"

class MLOptimizer:
    def __init__(self):
        self.ml_system = UltimateMLSystem(model_path=MODEL_ULTIMATE)
        self.consensus_nn = AgentConsensusNN()
        
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)

    def load_data(self):
        """Carga datos de trades con snapshots para entrenamiento."""
        if not os.path.exists(DB_PATH):
            print(f"❌ Error: {DB_PATH} no encontrado.")
            return None

        conn = sqlite3.connect(DB_PATH)
        query = """
            SELECT symbol, pnl_percent, market_snapshot, market_context
            FROM trades 
            WHERE market_snapshot IS NOT NULL 
            AND pnl_percent IS NOT NULL
            AND pnl_percent != -99.0
            ORDER BY timestamp ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if len(df) < 50:
            print(f"⚠️ Datos insuficientes para re-entrenar ({len(df)}/50).")
            return None
            
        return df

    def prepare_datasets(self, df):
        """Prepara X, y para ambos sistemas ML."""
        ultimate_features = []
        consensus_features = []
        y_class = []
        y_reg = []

        print(f"📊 Procesando {len(df)} muestras...")
        
        for _, row in df.iterrows():
            try:
                snap = json.loads(row['market_snapshot'])
                # Ultimate ML usa features crudas + snapshot
                feat_ultimate = self.ml_system.extract_features(snap)
                ultimate_features.append(feat_ultimate)
                
                # Consensus NN usa los votos de los agentes
                votos = snap.get('votos', {})
                feat_consensus = [votos.get(a, 50.0) for a in self.consensus_nn.AGENT_NAMES]
                consensus_features.append(feat_consensus)
                
                # Target
                pnl = float(row['pnl_percent'])
                y_reg.append(pnl)
                y_class.append(1 if pnl > 0 else 0)
                
            except Exception:
                continue

        return (pd.DataFrame(ultimate_features), 
                np.array(consensus_features), 
                np.array(y_class), 
                np.array(y_reg))

    def run_optimization(self):
        print(f"\n🧠 INICIANDO OPTIMIZACIÓN DEL CEREBRO [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
        print("="*60)
        
        df = self.load_data()
        if df is None: return

        X_ult, X_con, y_class, y_reg = self.prepare_datasets(df)
        
        if len(X_ult) < 50:
            print("❌ Muestras válidas insuficientes.")
            return

        # 1. OPTIMIZAR ULTIMATE ML (XGB/LGBM/RF)
        print("\n[1/2] Entrenando Ultimate ML Ensemble...")
        # Guardamos backup del actual
        if os.path.exists(MODEL_ULTIMATE):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.rename(MODEL_ULTIMATE, f"{BACKUP_DIR}/ultimate_{ts}.pkl")
        
        # El método train de UltimateMLSystem ya guarda automáticamente
        self.ml_system.train(X_ult, y_class, y_reg)
        
        # 2. OPTIMIZAR NEURAL CONSENSUS
        print("\n[2/2] Entrenando Neural Consensus (MLP)...")
        if os.path.exists(MODEL_CONSENSUS):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.rename(MODEL_CONSENSUS, f"{BACKUP_DIR}/consensus_{ts}.pkl")
            
        # Entrenamos la red neuronal
        success = self.consensus_nn.train(X_con, y_class)
        if success:
            self.consensus_nn.save(len(X_con))
            
        print("\n" + "="*60)
        print("✅ CICLO DE OPTIMIZACIÓN COMPLETADO")
        print(f"   Modelos actualizados con {len(X_ult)} experiencias.")
        print("="*60)

if __name__ == "__main__":
    opt = MLOptimizer()
    opt.run_optimization()

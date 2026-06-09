#!/usr/bin/env python3
"""
[V118-PRO] Script de Reseteo para Test de 5 Trades
==================================================
1. Realiza backup de la base de datos.
2. Limpia tablas de trades para iniciar de cero.
3. Mantiene la reputación y modelos de IA (opcional).
"""

import sqlite3
import shutil
import os
from datetime import datetime

DB_PATH = "sniper_brain.db"
BACKUP_DIR = "backups"

def reset_for_test():
    print("🧹 Iniciando protocolo de reseteo para test de 5 trades...")
    
    # 1. Crear directorio de backups si no existe
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"📁 Directorio {BACKUP_DIR} creado.")

    # 2. Realizar backup
    if os.path.exists(DB_PATH):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"sniper_brain_pre_test_{timestamp}.db")
        shutil.copy2(DB_PATH, backup_path)
        print(f"💾 Backup creado: {backup_path}")
    else:
        print("⚠️ No se encontró base de datos para respaldar.")
        return

    # 3. Limpiar tablas
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Limpiar trades (Reales y Shadow)
        cur.execute("DELETE FROM trades")
        print("🗑️ Tabla 'trades' limpiada.")
        
        # Opcional: Limpiar snapshots de error
        cur.execute("DELETE FROM error_snapshots")
        print("🗑️ Tabla 'error_snapshots' limpiada.")

        # Opcional: Limpiar madurez (Data Gate)
        if os.path.exists(".maturity_cache.pkl"):
            os.remove(".maturity_cache.pkl")
            print("🗑️ Cache de madurez eliminado.")

        conn.commit()
        conn.close()
        print("✅ Base de datos lista para el test.")
        
    except Exception as e:
        print(f"❌ Error durante el reseteo: {e}")

if __name__ == "__main__":
    confirm = input("⚠️ ¿ESTÁS SEGURO? Esto borrará el historial de trades (con backup). [y/N]: ")
    if confirm.lower() == 'y':
        reset_for_test()
    else:
        print("❌ Operación cancelada.")

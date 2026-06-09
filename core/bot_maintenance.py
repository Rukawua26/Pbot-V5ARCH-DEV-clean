import os
import shutil
from datetime import datetime


def backup_database_placeholder():
    """Realiza backup de los archivos críticos del bot."""
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_subdir = os.path.join(backup_dir, f"backup_{timestamp}")
    os.makedirs(backup_subdir, exist_ok=True)

    files_to_backup = [
        "sniper_brain.db",
        "ghost_brain.pkl",
        "ghost_brain_advanced.pkl",
        "agent_consensus_nn.pkl",
        "agent_models.pkl",
        "scaler.pkl",
    ]

    backed_up = []
    for file_name in files_to_backup:
        if os.path.exists(file_name):
            try:
                shutil.copy2(file_name, backup_subdir)
                backed_up.append(file_name)
            except Exception as error:
                print(f"⚠️ Error respaldando {file_name}: {error}")

    if backed_up:
        print(f"✅ Backup creado: {backup_subdir}")
        return backup_subdir

    print("⚠️ No hay archivos para respaldar")
    return None


def check_for_evolution(bot):
    """[v118] Entrenamiento automático basado en tiempo y trades."""
    last_train = bot.brain.get_last_train_timestamp()
    days_since_train = (datetime.now() - last_train).days

    # Reentrenar cada 7 días O si hay más de 100 nuevos trades
    cursor = bot.brain._get_conn().cursor()
    cursor.execute("SELECT COUNT(*) FROM trades WHERE timestamp > ?", (last_train.isoformat(),))
    new_trades = cursor.fetchone()[0]

    if days_since_train >= 7 or new_trades >= 100:
        bot.log(f"🧠 Reentrenando IA (días: {days_since_train}, trades nuevos: {new_trades})")
        bot.log(
            "ℹ️ Entrenamiento pendiente: usa /force_train para lanzar ghost_trainer "
            "en background. No se actualiza timestamp hasta que el entrenamiento real termine."
        )

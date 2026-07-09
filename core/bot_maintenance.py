import os
import shutil
from datetime import datetime

from config import Config
from core.execution_telemetry import append_execution_event


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
    run_genetic_batch(bot)

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


def _count_symbol_trades(bot, symbol: str) -> int:
    counter = getattr(bot.brain, "count_trades_for_symbol", None)
    if callable(counter):
        return int(counter(symbol))

    conn = bot.brain._get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM trades WHERE symbol = ? AND pnl_percent != -99.0",
            (symbol,),
        )
        row = cursor.fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def run_genetic_batch(bot) -> dict:
    pending = set(getattr(bot, "_genetic_batch_pending_symbols", set()) or set())
    if not bool(getattr(Config, "GENETIC_BATCH_ENABLED", True)):
        append_execution_event(bot, "GENETIC_BATCH_SKIPPED", {"reason": "DISABLED"})
        return {"status": "SKIPPED", "reason": "DISABLED", "processed": 0, "mutated": 0}
    if not pending:
        append_execution_event(bot, "GENETIC_BATCH_SKIPPED", {"reason": "NO_PENDING_SYMBOLS"})
        return {"status": "SKIPPED", "reason": "NO_PENDING_SYMBOLS", "processed": 0, "mutated": 0}

    min_trades = int(getattr(Config, "GENETIC_BATCH_MIN_TRADES", 50) or 50)
    append_execution_event(
        bot,
        "GENETIC_BATCH_STARTED",
        {"pending_symbols": len(pending), "min_trades": min_trades},
    )

    processed = 0
    mutated = 0
    still_pending = set(pending)
    for symbol in sorted(pending):
        samples = _count_symbol_trades(bot, symbol)
        if samples < min_trades:
            append_execution_event(
                bot,
                "GENETIC_BATCH_SKIPPED",
                {"symbol": symbol, "reason": "INSUFFICIENT_TRADES", "samples": samples},
            )
            continue

        processed += 1
        if bot.brain.evolve_genetics(symbol):
            mutated += 1
            bot.log(f"🧬 ADN MUTADO: {symbol} ha evolucionado sus parámetros SL/TP.")
            append_execution_event(bot, "GENETIC_BATCH_SWAP_APPLIED", {"symbol": symbol})
        still_pending.discard(symbol)

    bot._genetic_batch_pending_symbols = still_pending
    append_execution_event(
        bot,
        "GENETIC_BATCH_COMPLETED",
        {"processed": processed, "mutated": mutated, "remaining": len(still_pending)},
    )
    return {"status": "COMPLETED", "processed": processed, "mutated": mutated}

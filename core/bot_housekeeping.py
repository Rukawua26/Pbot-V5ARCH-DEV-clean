import os
import subprocess
import sys
import time

from tools.notifier import send_telegram_msg


def run_periodic_housekeeping(bot, now, last_report_time, last_coach_time, last_log_check):
    if now.hour == 23 and now.minute == 0 and not getattr(bot, "day_report_sent", False):
        bot.log("📊 Enviando reporte diario 23:00...")
        from reporter import generate_mobile_report

        send_telegram_msg("📅 *REPORTE DE CIERRE DIARIO*\n" + generate_mobile_report(bot.balance))
        bot.day_report_sent = True
    if now.hour == 0:
        bot.day_report_sent = False

    if now.hour == 0 and now.minute == 0 and not getattr(bot, "daily_backup_done", False):
        backup_fn = getattr(bot, "_backup_database_fn", None)
        if backup_fn:
            bot.log("🛡️ Iniciando backup diario automático...")
            try:
                backup_fn()
            except Exception as error:
                bot.log(f"⚠️ Error backup diario: {error}")
        bot.daily_backup_done = True
    if now.hour == 1:
        bot.daily_backup_done = False

    if time.time() - last_report_time > (4 * 3600):
        bot.log("📱 Enviando reporte móvil automático...")
        from reporter import generate_mobile_report

        rep = generate_mobile_report(bot.balance)
        send_telegram_msg(rep)
        last_report_time = time.time()

    if time.time() - last_coach_time > 3600:
        project_root = os.path.dirname(os.path.dirname(__file__))
        coach_path = os.path.join(project_root, "tools", "ai_coach.py")
        if os.path.exists(coach_path):
            bot.log("🧠 Ejecutando AI Coach programado...")
            try:
                subprocess.run([sys.executable, coach_path, "--silent"], check=False, timeout=900)
                bot.log("✅ AI Coach finalizado.")
            except Exception as error:
                bot.log(f"⚠️ Error AI Coach auto: {error}")
        else:
            if not getattr(bot, "_ai_coach_missing_logged", False):
                bot.log("ℹ️ AI Coach auto desactivado: tools/ai_coach.py no encontrado.")
                bot._ai_coach_missing_logged = True
        last_coach_time = time.time()

    if time.time() - getattr(bot, "last_ml_health_check", 0) > 1800:
        if getattr(bot, "_ml_monitor_available", False) and bot.ml_monitor:
            bot.log("🔍 Verificando salud de modelos ML...")
            bot.ml_healthy = bot._check_ml_models_health()
            if bot.ml_healthy:
                bot.log("✅ ML Models OK")
        bot.last_ml_health_check = time.time()

    if time.time() - last_log_check > 1800:
        last_log_check = time.time()

    if time.time() - getattr(bot, "last_perf_check", 0) > 3600:
        with bot.db_lock:
            drop_detected, curr_wr, prev_wr = bot.brain.check_performance_drop()
        if drop_detected:
            bot.log(f"🚨 ALERTA DE RENDIMIENTO: WR cayó de {prev_wr:.1f}% a {curr_wr:.1f}%")
            send_telegram_msg(
                f"🚨 *ALERTA CRÍTICA: CAÍDA DE RENDIMIENTO*\n"
                f"El Win Rate ha caído un *{prev_wr - curr_wr:.1f}%* en 24h.\n"
                f"📉 Ayer: {prev_wr:.1f}% | Hoy: {curr_wr:.1f}%\n"
                f"⚠️ *Sugerencia:* Considere revertir cambios recientes (Rollback)."
            )
        bot.last_perf_check = time.time()

    return last_report_time, last_coach_time, last_log_check

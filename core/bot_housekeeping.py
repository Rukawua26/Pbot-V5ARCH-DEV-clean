import subprocess
import sys
import time

from config import Config
from core.model_loader import ROOT, resolve_script_path
from core.runtime_metrics import append_runtime_metric
from tools.notifier import send_telegram_msg


def _send_mobile_report(bot, *, prefix="", log_message="") -> bool:
    now_ts = time.time()
    retry_after = float(getattr(bot, "_mobile_report_retry_after", 0.0) or 0.0)
    if now_ts < retry_after:
        return False

    if log_message:
        bot.log(log_message)

    try:
        from tools.reporter import generate_mobile_report

        report = generate_mobile_report(bot.balance)
        if not send_telegram_msg(prefix + report):
            raise RuntimeError("Telegram report was not queued")
    except Exception as error:
        failures = int(getattr(bot, "_mobile_report_failure_count", 0) or 0) + 1
        retry_seconds = min(3600, 60 * (2 ** min(failures - 1, 6)))
        bot._mobile_report_failure_count = failures
        bot._mobile_report_retry_after = now_ts + retry_seconds
        bot._mobile_report_last_error = str(error)[:180]
        bot.log(
            f"⚠️ Reporte móvil falló; reintento en {retry_seconds}s: {bot._mobile_report_last_error}"
        )
        append_runtime_metric(
            "mobile_report",
            {
                "ok": False,
                "failure_count": failures,
                "retry_seconds": retry_seconds,
                "error_type": type(error).__name__,
                "error": bot._mobile_report_last_error,
            },
        )
        return False

    bot._mobile_report_failure_count = 0
    bot._mobile_report_retry_after = 0.0
    bot._mobile_report_last_success = now_ts
    bot._mobile_report_last_error = ""
    append_runtime_metric("mobile_report", {"ok": True})
    return True


def run_periodic_housekeeping(bot, now, last_report_time, last_coach_time, last_log_check):
    reports_enabled = bool(getattr(Config, "AUTO_MOBILE_REPORTS_ENABLED", True))
    if (
        reports_enabled
        and now.hour == 23
        and now.minute == 0
        and not getattr(bot, "day_report_sent", False)
    ):
        if _send_mobile_report(
            bot,
            prefix="📅 *REPORTE DE CIERRE DIARIO*\n",
            log_message="📊 Enviando reporte diario 23:00...",
        ):
            bot.day_report_sent = True
            last_report_time = time.time()
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

    if reports_enabled and time.time() - last_report_time > (4 * 3600):
        if _send_mobile_report(bot, log_message="📱 Enviando reporte móvil automático..."):
            last_report_time = time.time()

    if time.time() - last_coach_time > 3600:
        try:
            coach_path = resolve_script_path(ROOT / "tools" / "ai_coach.py")
            if coach_path.exists():
                bot.log("🧠 Ejecutando AI Coach programado...")
                try:
                    subprocess.run(
                        [sys.executable, str(coach_path), "--silent"],
                        check=False,
                        timeout=300,
                    )
                    bot.log("✅ AI Coach finalizado.")
                except Exception as error:
                    bot.log(f"⚠️ Error AI Coach auto: {error}")
            else:
                if not getattr(bot, "_ai_coach_missing_logged", False):
                    bot.log("ℹ️ AI Coach auto desactivado: tools/ai_coach.py no encontrado.")
                    bot._ai_coach_missing_logged = True
        except Exception:
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

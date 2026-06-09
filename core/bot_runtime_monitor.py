import json
import os
import time

from config import Config
from core.metrics_export import export_metrics_summary
from core.time_utils import monotonic_now, utc_now_iso


def _rotate_jsonl(path: str, max_bytes: int, backups: int) -> None:
    if max_bytes <= 0 or backups <= 0:
        return
    if not os.path.exists(path):
        return
    if os.path.getsize(path) < max_bytes:
        return

    oldest = f"{path}.{backups}"
    if os.path.exists(oldest):
        os.remove(oldest)

    for idx in range(backups - 1, 0, -1):
        src = f"{path}.{idx}"
        dst = f"{path}.{idx + 1}"
        if os.path.exists(src):
            os.replace(src, dst)

    os.replace(path, f"{path}.1")


def get_rss_mb(bot) -> float:
    """Read process RSS memory in MB without extra dependencies."""
    try:
        with open("/proc/self/status", encoding="utf-8") as file_obj:
            for line in file_obj:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        kb = float(parts[1])
                        return kb / 1024.0
    except Exception as error:
        bot.log(f"⚠️ No se pudo leer RSS del proceso: {error}")
    return 0.0


def append_runtime_metric(bot, payload) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        metrics_path = "logs/runtime_metrics.jsonl"
        max_bytes = int(os.getenv("RUNTIME_METRICS_MAX_BYTES", "5242880"))
        backups = int(os.getenv("RUNTIME_METRICS_BACKUPS", "3"))
        _rotate_jsonl(metrics_path, max_bytes=max_bytes, backups=backups)
        with open(metrics_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as error:
        bot.log(f"⚠️ Error guardando runtime metric: {error}")


def run_runtime_monitor_loop(bot):
    """Continuous profiling to detect spin-lock and memory leaks."""
    bot._perf_start_rss_mb = get_rss_mb(bot)
    last_mono = monotonic_now()
    last_cpu = os.times().user + os.times().system

    # Agent weight decay monitor
    weight_monitor = None
    try:
        from core.strategy.weight_monitor import AgentWeightMonitor

        if getattr(bot, "brain", None) is not None:
            weight_monitor = AgentWeightMonitor(bot.brain)
    except Exception as exc:
        bot.log(f"⚠️ Weight monitor init skipped: {exc}")
    weight_check_counter = 0

    while bot.is_running:
        time.sleep(60)
        now_mono = monotonic_now()
        cpu_now = os.times().user + os.times().system

        wall_delta = max(now_mono - last_mono, 1e-6)
        cpu_delta = max(cpu_now - last_cpu, 0.0)
        cpu_pct = (cpu_delta / wall_delta) * 100.0

        last_mono = now_mono
        last_cpu = cpu_now

        rss_mb = get_rss_mb(bot)
        elapsed = now_mono - float(getattr(bot, "_perf_start_ts", now_mono))

        loops = bot._guardian_stats.get("loops", 0)
        work_s = bot._guardian_stats.get("work_s", 0.0)
        sleep_s = bot._guardian_stats.get("sleep_s", 0.0)
        busy_pct = (work_s / max(work_s + sleep_s, 1e-6)) * 100.0

        metric = {
            "ts": utc_now_iso(),
            "uptime_s": round(elapsed, 2),
            "rss_mb": round(rss_mb, 2),
            "cpu_pct": round(cpu_pct, 2),
            "guardian_loops": int(loops),
            "guardian_busy_pct": round(busy_pct, 2),
            "guardian_bailouts": int(bot._guardian_stats.get("bailout_count", 0)),
        }
        append_runtime_metric(bot, metric)

        if int(elapsed) % 300 < 60:
            bot.log(
                f"📈 PERF: RSS={rss_mb:.1f}MB | CPU={cpu_pct:.1f}% | GUARDIAN busy={busy_pct:.1f}% loops={loops}"
            )

        if (not bot._perf_h1_logged) and elapsed >= 3600:
            delta = rss_mb - bot._perf_start_rss_mb
            bot.log(
                f"🧪 MEMORY H1: inicio={bot._perf_start_rss_mb:.1f}MB -> h1={rss_mb:.1f}MB (delta {delta:+.1f}MB)"
            )
            bot._perf_h1_logged = True

        if (not bot._perf_h24_logged) and elapsed >= 86400:
            delta = rss_mb - bot._perf_start_rss_mb
            status = "OK" if rss_mb <= 800 else "ALERTA"
            bot.log(
                f"🧪 MEMORY H24: inicio={bot._perf_start_rss_mb:.1f}MB -> h24={rss_mb:.1f}MB (delta {delta:+.1f}MB) | {status}"
            )
            bot._perf_h24_logged = True

        try:
            export_metrics_summary(bot)
        except Exception as exc:
            bot.log(f"⚠️ Metrics export error: {exc}")

        # Periodic agent weight degradation check (every ~60min)
        weight_check_counter += 1
        if (
            weight_monitor is not None
            and weight_check_counter >= Config.AGENT_MONITOR_INTERVAL_MINUTES
        ):
            weight_check_counter = 0
            try:
                weight_monitor.run_check(bot=bot)
            except Exception as exc:
                bot.log(f"⚠️ Weight monitor error: {exc}")

import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger("SniperAI")


def _rotate_existing(path, backups=3):
    for i in range(backups, 0, -1):
        old = f"{path}.{i}"
        if os.path.exists(old):
            if i == backups:
                os.remove(old)
            else:
                os.replace(f"{path}.{i}", f"{path}.{i + 1}")
    if os.path.exists(path):
        os.replace(path, f"{path}.1")


def _rotate_jsonl(path, max_bytes=5242880, backups=3):
    try:
        if os.path.exists(path) and os.path.getsize(path) >= max_bytes:
            _rotate_existing(path, backups)
    except Exception as error:
        logger.warning("⚠️ Rotación JSONL falló para %s: %s", path, error)


def _should_skip_file_telemetry() -> bool:
    if str(os.getenv("SNIPER_DISABLE_FILE_TELEMETRY", "0")).strip() == "1":
        return True
    return False


def append_execution_event(bot, event: str, payload: dict) -> None:
    try:
        if _should_skip_file_telemetry():
            return
        os.makedirs("logs", exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": str(event),
            "payload": payload or {},
        }
        events_path = "logs/execution_events.jsonl"
        max_bytes = int(os.getenv("EXECUTION_EVENTS_MAX_BYTES", "5242880"))
        backups = int(os.getenv("EXECUTION_EVENTS_BACKUPS", "3"))
        _rotate_jsonl(events_path, max_bytes=max_bytes, backups=backups)
        with open(events_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as error:
        bot.log(f"⚠️ Error guardando execution event: {error}")
